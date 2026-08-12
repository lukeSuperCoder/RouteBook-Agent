import logging
from uuid import uuid4

from langgraph.types import Command

from .graph import build_graph, initial_state


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="\033[33m[%(asctime)s %(levelname)s %(name)s]\033[0m %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx 的 INFO 日志包含完整 query string，高德 Key 位于 query 参数中。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    try:
        graph = build_graph()
    except RuntimeError as exc:
        raise SystemExit(f"配置错误：{exc}") from exc
    message = input(
        "请输入旅行需求（直接回车使用示例）：\n> "
    ).strip() or "国庆去南京三天，想去中山陵、夫子庙和鼓楼"
    config = {"configurable": {"thread_id": f"routebook:demo:{uuid4()}"}}
    logging.getLogger("routebook.cli").info(
        "启动工作流 thread_id=%s",
        config["configurable"]["thread_id"],
    )

    try:
        result = graph.invoke(initial_state(message), config=config)
    except Exception as exc:
        raise SystemExit(f"工作流调用失败：{exc}") from exc

    print(
        f"\nAI 提取结果：目的地={result['destination']}，"
        f"天数={result['days']}，必去地点={result['must_visit']}"
    )
    print(f"AI 推荐地点：{result['suggested_visit']}")

    while result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        print(f"\n流程已暂停：{payload['question']}")
        for index, candidate in enumerate(payload["candidates"], start=1):
            print(
                f"  {index}. {candidate['name']}"
                f"（{candidate['district']} {candidate['address']}，"
                f"{candidate['longitude']},{candidate['latitude']}）"
            )

        while True:
            raw_choice = input("请选择序号：\n> ").strip()
            if raw_choice.isdigit() and 1 <= int(raw_choice) <= len(payload["candidates"]):
                break
            print("请输入列表中的有效序号。")

        selected = payload["candidates"][int(raw_choice) - 1]
        logging.getLogger("routebook.cli").info(
            "用户选择地点 place_id=%s name=%s，恢复工作流",
            selected["id"],
            selected["name"],
        )
        try:
            result = graph.invoke(Command(resume=selected["id"]), config=config)
        except Exception as exc:
            raise SystemExit(f"工作流恢复失败：{exc}") from exc
        print(f"\n流程已恢复，确认地点：{selected['name']}")

    print("\n高德确认地点：")
    for place in result["confirmed_places"]:
        print(
            f"- {place['name']}｜{place['district']} {place['address']}｜"
            f"{place['longitude']},{place['latitude']}（GCJ-02）"
        )

    print("\n生成结果：")
    for day in result["itinerary"]:
        print(f"- {day}")
    print(f"\n工作流状态：{result['stage']}")


if __name__ == "__main__":
    main()
