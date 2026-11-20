from typing import TypedDict, List, Annotated
import operator


class AgentState(TypedDict):

    messages: Annotated[List[dict], operator.add]
    current_query: str
    route: str          # planner decision: kubernetes / conversational / off_topic / unsafe / unsupported_task
    skipped_part: str   # out-of-scope part of a mixed question ("" if none)
    documents: List[str]
    plan: List[str]
    status: str
    final_answer: str