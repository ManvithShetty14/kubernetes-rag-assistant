"""
Scope of the assistant: Kubernetes ONLY.
Single place for the routing labels and the fixed (non-LLM) replies, so the planner,
graph, refuser and responder all agree.
"""

# Planner chooses the route
ROUTE_KUBERNETES = "kubernetes"            # search the docs and answer
ROUTE_CONVERSATIONAL = "conversational"    # greetings / questions about the chat itself
ROUTE_OFF_TOPIC = "off_topic"              # not about Kubernetes at all
ROUTE_UNSAFE = "unsafe"                    # attacking / unauthorised access / causing harm
ROUTE_UNSUPPORTED_TASK = "unsupported_task"  # Kubernetes-themed creative tasks (poems, stories...)
ROUTE_JAILBREAK = "jailbreak"              # tries to override instructions / change persona / leak the prompt

REFUSAL_ROUTES = {ROUTE_OFF_TOPIC, ROUTE_UNSAFE, ROUTE_UNSUPPORTED_TASK, ROUTE_JAILBREAK}

CONVERSATIONAL = "CONVERSATIONAL"
OFF_TOPIC = "OFF_TOPIC"

# --- Fixed replies (no LLM call) ----
OFF_TOPIC_MESSAGE = (
    "I'm a Kubernetes documentation assistant, so I can only help with Kubernetes questions "
    "(pods, deployments, Jobs, CronJobs, autoscaling, cluster architecture and so on). "
    "Please ask me something about Kubernetes!"
)

UNSAFE_MESSAGE = (
    "I can't help with accessing, attacking or disrupting systems you don't own or aren't "
    "authorised to manage. I'm happy to help you secure your own cluster instead — for example "
    "with RBAC, network policies, secrets management or audit logging."
)

UNSUPPORTED_TASK_MESSAGE = (
    "I answer questions from the Kubernetes documentation, so I don't write poems, stories, "
    "songs or jokes. Want me to explain a Kubernetes concept instead — for example how "
    "pods, Jobs or autoscaling work?"
)

# Same text as the NeMo jailbreak rail, so the user sees one consistent reply whichever layer caught it
JAILBREAK_MESSAGE = (
    "I maintain consistent guidelines regardless of how I am prompted. "
    "I am here to help with Kubernetes. What can I help you with?"
)

NOT_FOUND_MESSAGE = (
    "I couldn't find anything about that in the Kubernetes documentation I have access to. "
    "Try rephrasing your question, or ask about another Kubernetes topic."
)

REFUSAL_MESSAGES = {
    ROUTE_OFF_TOPIC: OFF_TOPIC_MESSAGE,
    ROUTE_UNSAFE: UNSAFE_MESSAGE,
    ROUTE_UNSUPPORTED_TASK: UNSUPPORTED_TASK_MESSAGE,
    ROUTE_JAILBREAK: JAILBREAK_MESSAGE,
}


def skipped_note(skipped_part: str) -> str:
    """Appended to an answer when part of a mixed question was out of scope."""
    return f"\n\n---\n*Note: I skipped {skipped_part}, since I can only help with Kubernetes.*"
