from langgraph.graph import StateGraph, END
from schemas import OCRState
from nodes import (
    convert_to_pdf_node, validate_pdf_node, run_ocr_node,
    extract_text_node, extract_images_node, caption_images_node, merge_content_node,
)


def _route_after_convert(state: OCRState) -> str:
    return END if state.get("error") else "validate_pdf_node"


def _route_after_validate(state: OCRState) -> str:
    return END if state.get("error") else "run_ocr_node"


def _route_after_ocr(state: OCRState) -> str:
    return END if state.get("error") else "extract_text_node"


def _route_after_images(state: OCRState) -> str:
    return END if state["status"] == "failed" else "caption_images_node"


def _route_after_captions(state: OCRState) -> str:
    return END if state["status"] == "failed" else "merge_content_node"


graph = StateGraph(OCRState)

graph.add_node("convert_to_pdf_node", convert_to_pdf_node)
graph.add_node("validate_pdf_node", validate_pdf_node)
graph.add_node("run_ocr_node", run_ocr_node)
graph.add_node("extract_text_node", extract_text_node)
graph.add_node("extract_images_node", extract_images_node)
graph.add_node("caption_images_node", caption_images_node)
graph.add_node("merge_content_node", merge_content_node)

graph.set_entry_point("convert_to_pdf_node")
graph.add_conditional_edges("convert_to_pdf_node", _route_after_convert)
graph.add_conditional_edges("validate_pdf_node", _route_after_validate)
graph.add_conditional_edges("run_ocr_node", _route_after_ocr)
graph.add_edge("extract_text_node", "extract_images_node")
graph.add_conditional_edges("extract_images_node", _route_after_images)
graph.add_conditional_edges("caption_images_node", _route_after_captions)
graph.add_edge("merge_content_node", END)

pipeline = graph.compile()
