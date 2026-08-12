from langgraph.checkpoint.postgres import PostgresSaver

from .config import get_settings


def main() -> None:
    settings = get_settings()
    with PostgresSaver.from_conn_string(settings.langgraph_database_url) as checkpointer:
        checkpointer.setup()


if __name__ == "__main__":
    main()
