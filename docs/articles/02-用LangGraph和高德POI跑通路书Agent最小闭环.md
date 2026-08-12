> 这是 RouteBook Agent 开发记录的第二篇。上一篇还停留在需求梳理和技术方案，这一次终于开始写代码了。
> 
> 目标很简单：先让用户输入一句“帮我规划一个xx地方的几日游”，系统能够理解需求、找到真实地点，并生成一份最简单的分日行程。


项目地址：[github.com/lukeSuperCoder/RouteBook-Agent](https://github.com/lukeSuperCoder/RouteBook-Agent)

相关官网与文档：

- [LangGraph 官方文档](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph Graph API 指南](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [LangGraph Python API Reference](https://reference.langchain.com/python/langgraph/overview)
- [智谱 GLM-5 模型文档](https://docs.bigmodel.cn/cn/guide/models/text/glm-5)
- [智谱 Claude API 兼容文档](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)
- [高德开放平台](https://lbs.amap.com/)
- [高德 POI 搜索 2.0](https://lbs.amap.com/api/webservice/guide/api/newpoisearch)
- [高德路径规划 2.0](https://lbs.amap.com/api/webservice/guide/api/newroute)



## 先把最小流程跑起来

我一开始没有马上连接模型和地图，而是用假数据搭了一张最小的 LangGraph：

```text
提取需求 → 搜索地点 → 判断候选 → 生成行程
                         └→ 等待用户确认
```

这个版本看起来很简陋，但它先验证了两个最重要的能力：流程可以根据状态选择下一步，也可以在地点不明确时暂停，等用户选择后再从原位置继续。

相比让模型一次性输出整篇攻略，这种方式写起来更慢，却让我第一次清楚地看到 Agent 正在做什么。模型只是流程中的一个节点，而不是包办所有事情的黑盒。

在实现上，我把用户需求、待搜索地点、候选地点和已确认地点都放进 Graph State。节点只返回自己修改的字段，下一步由边决定：

```python
builder = StateGraph(RouteBookState)
builder.add_node("extract_requirements", extract_requirements)
builder.add_node("search_places", search_places)
builder.add_node("confirm_place", confirm_place)
builder.add_node("build_itinerary", build_itinerary)

builder.add_edge(START, "extract_requirements")
builder.add_edge("extract_requirements", "search_places")
builder.add_conditional_edges("search_places", route_after_search)
builder.add_edge("confirm_place", "search_places")
builder.add_edge("build_itinerary", END)
```

这里没有复杂的多 Agent 协作，本质上就是一张带循环和人工确认的状态图。对当前阶段来说，这比追求高度自治更重要。

## 接入模型后，第一次结果并不理想

接下来我用 Anthropic 兼容接口接入了 GLM-5，通过 Tool Calling 提取目的地、天数和必去地点。

第一次真实输入是：

```text
北京三日游
```

模型正确识别出了北京和三天，但必去地点是空数组。最终程序给出的结果是：

```text
第一天：自由探索与休息
第二天：自由探索与休息
第三天：自由探索与休息
```

从程序角度看，它没有出错。真正的问题是我给模型的规则太保守：只允许提取用户明确说出的地点，不能主动推荐。对于“北京三日游”这种宽泛需求，它只能忠实地返回空结果。

后来我把需求拆成了两部分：

- `must_visit`：用户明确指定的地点；
- `suggested_visit`：AI 补充的推荐地点。

这样既不会把模型建议伪装成用户要求，也能让信息较少的输入继续向下执行。再次测试时，模型推荐了故宫博物院、天安门广场和八达岭长城。

为了避免模型自由输出一段难以解析的文字，我通过 Tool Calling 约束返回结构：

```json
{
  "destination": "北京",
  "days": 3,
  "must_visit": [],
  "suggested_visit": [
    "故宫博物院",
    "天安门广场",
    "八达岭长城"
  ]
}
```

模型的结果还会经过普通代码校验、去重和数量限制，再进入后续节点。也就是说，Tool Calling 解决的是输出格式问题，并不代表模型输出可以跳过业务校验。

这次调试让我意识到，很多所谓的“模型效果问题”，其实是产品语义没有定义清楚。Prompt 并不能替代字段设计。

## 地点必须交给真实地图验证

模型可以推荐故宫，但不应该生成故宫的正式坐标。因此下一步接入了高德地点搜索 2.0，让每个推荐地点都经过真实 POI 查询。

高德搜索被封装成独立适配器，Graph 节点只传入地点名称和城市，不接触 API Key，也不直接使用供应商原始响应：

```python
response = client.get(
    f"{base_url}/v5/place/text",
    params={
        "key": api_key,
        "keywords": keyword,
        "region": destination,
        "city_limit": "true",
        "page_size": 5,
    },
)
```

适配器会同时检查 HTTP 状态、高德 `status` 和 `infocode`，然后将结果统一转换为内部地点对象，保留高德 POI ID、地址、行政区、类型以及 GCJ-02 经纬度。这样以后替换接口或增加其他地图来源时，工作流本身不需要跟着重写。

故宫博物院和天安门广场都找到了唯一的精确候选，流程自动确认。搜索八达岭长城时，高德返回了景区、直通车、游客服务中心、北城等多个结果。

这正是我想保留的场景：系统不能为了流程顺畅就默认取第一条，而是通过 LangGraph 的 `interrupt()` 暂停，把候选交给用户。用户选择后，再用 `Command(resume=...)` 恢复工作流。

核心代码其实很短：

```python
selected_id = interrupt({
    "type": "place_disambiguation",
    "place_name": state["current_place_name"],
    "candidates": state["candidates"],
})

# 用户选择后，使用相同的 thread_id 恢复
graph.invoke(
    Command(resume=selected_place_id),
    config=config,
)
```

真正需要注意的是，恢复时当前节点会重新执行。因此暂停前不能随意写入正式路书版本，外部副作用也必须可重复执行或具备幂等保护。

最终跑通的过程变成了：

```text
自然语言需求
→ AI 提取与推荐
→ 高德查询真实 POI
→ 唯一地点自动确认
→ 歧义地点暂停选择
→ 恢复流程
→ 生成分日行程
```

到这里，一个很小但完整的闭环终于成立了。

## 真实接口总会带来一些意外

端到端测试时还遇到了两个很实际的问题。

第一个是高德返回 `10021`，提示调用频率超限。原因是程序在很短时间内连续搜索了五个推荐地点。后来我把推荐数量控制为大约一天一个主要地点，并在高德适配器里增加请求间隔。

第二个问题更值得警惕：`httpx` 的 INFO 日志输出了完整请求 URL，而高德 Key 正好位于 Query 参数中。虽然业务代码没有主动打印 Key，它还是出现在了终端里。现在我关闭了第三方 HTTP 请求详情，只保留脱敏后的业务日志。

这两件事都是只看接口文档很难感受到的。真正拿 Key 跑一次，才能发现限流、日志和错误映射也是功能的一部分。

## 这一阶段的体会

写完这个原型后，我对 Agent 应用的理解更具体了一些。

LangGraph 的价值不在于让流程看起来更“智能”，而在于把不确定性关进明确的边界里：模型负责理解和建议，高德负责地点事实，程序负责确认规则，用户保留关键决定权。

目前生成的还不是一份成熟路书。它只是把确认后的地点平均放到几天里，还没有计算地点之间的距离、交通时间和合理顺序。但这个最小闭环已经验证了后续路线规划所需的基础：我们拿到了经过确认的真实地点 ID 和 GCJ-02 坐标，也有了一条可以暂停、恢复和继续扩展的工作流。

下一步，我会在这些真实地点之上接入高德路径规划，尝试解决“每天怎么排、地点之间怎么走、时间是否合理”这几个更接近路书核心的问题。
