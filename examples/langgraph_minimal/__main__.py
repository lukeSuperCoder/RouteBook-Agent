from uuid import uuid4

from langgraph.types import Command

from .graph import build_graph, initial_state


def main() -> None:
    graph = build_graph()
    message = input(
        "请输入旅行需求（直接回车使用示例）：\n> "
    ).strip() or "国庆去南京三天，想去中山陵、夫子庙和鼓楼"
    config = {"configurable": {"thread_id": f"routebook:demo:{uuid4()}"}}

    result = graph.invoke(initial_state(message), config=config)

    if result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        print(f"\n流程已暂停：{payload['question']}")
        for index, candidate in enumerate(payload["candidates"], start=1):
            print(f"  {index}. {candidate['name']}（{candidate['address']}）")

        while True:
            raw_choice = input("请选择序号：\n> ").strip()
            if raw_choice.isdigit() and 1 <= int(raw_choice) <= len(payload["candidates"]):
                break
            print("请输入列表中的有效序号。")

        selected = payload["candidates"][int(raw_choice) - 1]
        result = graph.invoke(Command(resume=selected["id"]), config=config)
        print(f"\n流程已恢复，确认地点：{selected['name']}")

    print("\n生成结果：")
    for day in result["itinerary"]:
        print(f"- {day}")
    print(f"\n工作流状态：{result['stage']}")


if __name__ == "__main__":
    main()
