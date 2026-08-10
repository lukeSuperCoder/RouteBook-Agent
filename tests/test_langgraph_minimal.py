from langgraph.types import Command

from examples.langgraph_minimal.graph import build_graph, initial_state


def test_graph_interrupts_and_resumes() -> None:
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-place-confirmation"}}

    interrupted = graph.invoke(
        initial_state("去南京三天，想去鼓楼"),
        config=config,
    )

    assert interrupted["stage"] == "places_searched"
    assert interrupted["__interrupt__"][0].value["type"] == "place_disambiguation"

    completed = graph.invoke(
        Command(resume="gulou_park"),
        config=config,
    )

    assert completed["stage"] == "completed"
    assert completed["confirmed_place_id"] == "gulou_park"
    assert len(completed["itinerary"]) == 3
    assert "鼓楼公园" in completed["itinerary"][1]
