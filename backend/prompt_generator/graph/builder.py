"""Assemble and compile the LangGraph invoice pipeline."""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from prompt_generator.graph.edges import (
    route_after_feedback,
    route_after_prompt_gen,
    route_after_splitter,
    route_after_validation,
)
from prompt_generator.graph.nodes import (
    docling_retry_node,
    extraction_node,
    feedback_node,
    merge_node,
    page_splitter_node,
    per_page_classifier_node,
    prompt_gen_node,
    prompt_node,
    validation_node,
)
from prompt_generator.graph.state import InvoiceState


@lru_cache(maxsize=1)
def build_app():
    workflow = StateGraph(InvoiceState)

    workflow.add_node("page_splitter", page_splitter_node)
    workflow.add_node("classifier", per_page_classifier_node)
    workflow.add_node("merge", merge_node)
    workflow.add_node("prompt_gen", prompt_gen_node)
    workflow.add_node("extraction", extraction_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("feedback", feedback_node)
    workflow.add_node("docling_retry", docling_retry_node)
    workflow.add_node("prompt", prompt_node)

    workflow.set_entry_point("page_splitter")
    workflow.add_conditional_edges(
        "page_splitter",
        route_after_splitter,
        {"classifier": "classifier", "end": END},
    )
    workflow.add_edge("classifier", "merge")
    workflow.add_edge("merge", "prompt_gen")
    workflow.add_conditional_edges(
        "prompt_gen",
        route_after_prompt_gen,
        {"extraction": "extraction", "end": END},
    )
    workflow.add_edge("extraction", "validation")
    workflow.add_conditional_edges(
        "validation",
        route_after_validation,
        {"feedback": "feedback", "prompt": "prompt", "end": END},
    )
    workflow.add_conditional_edges(
        "feedback",
        route_after_feedback,
        {"docling_retry": "docling_retry", "prompt": "prompt"},
    )
    workflow.add_edge("docling_retry", "extraction")
    workflow.add_edge("prompt", END)

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
