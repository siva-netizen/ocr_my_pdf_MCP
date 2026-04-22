from langgraph.graph import StateGraph, END
from schemas import OCRState
from nodes import validate_pdf_node, run_ocr_node, extract_text_node, format_response_node


def _route_after_validate(state: OCRState) -> str:
    return END if state.get("error") else "run_ocr_node"


def _route_after_ocr(state: OCRState) -> str:
    return END if state.get("error") else "extract_text_node"


graph = StateGraph(OCRState)

graph.add_node("validate_pdf_node", validate_pdf_node)
graph.add_node("run_ocr_node", run_ocr_node)
graph.add_node("extract_text_node", extract_text_node)
graph.add_node("format_response_node", format_response_node)

graph.set_entry_point("validate_pdf_node")
graph.add_conditional_edges("validate_pdf_node", _route_after_validate)
graph.add_conditional_edges("run_ocr_node", _route_after_ocr)
graph.add_edge("extract_text_node", "format_response_node")
graph.add_edge("format_response_node", END)

pipeline = graph.compile()
