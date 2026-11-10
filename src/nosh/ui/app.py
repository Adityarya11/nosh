"""Streamlit app: Markdown chat in the main pane; mode selector, removable filters and build stats in the sidebar."""

import streamlit as st

from nosh.chat import turn
from nosh.llm import LLMUnavailable, check_ready
from nosh.session import SessionState
from nosh.store import meta, totals

MODES = ["auto", "search", "match", "substitute"]


def _session() -> SessionState:
    if "session" not in st.session_state:
        st.session_state.session = SessionState()
        st.session_state.messages = []
    return st.session_state.session


def _remove_filter(session: SessionState, kind: str, value: str) -> None:
    lists = {"group": session.filters.exclude_groups, "ingredient": session.filters.exclude_ingredients, "pref": session.filters.preferences}
    lists[kind].remove(value)


def _sidebar(session: SessionState) -> None:
    st.sidebar.title("nosh")
    session.mode = st.sidebar.selectbox("Mode", MODES, index=MODES.index(session.mode))
    st.sidebar.subheader("Active filters")
    f = session.filters
    items = [("group", g, f"no {g}") for g in f.exclude_groups] + [("ingredient", i, f"no {i}") for i in f.exclude_ingredients]
    items += [("pref", p, p) for p in f.preferences]
    for kind, value, label in items:
        if st.sidebar.button(f"x {label}", key=f"rm-{kind}-{value}"):
            _remove_filter(session, kind, value)
            st.rerun()
    if f.max_steps is not None and st.sidebar.button(f"x at most {f.max_steps} steps", key="rm-steps"):
        f.max_steps = None
        st.rerun()
    if not items and f.max_steps is None:
        st.sidebar.caption("none")
    st.sidebar.subheader("Last results")
    for rid, title in zip(session.last_results, session.last_titles):
        st.sidebar.caption(f"[{rid}] {title}")
    usable, dropped = totals()
    st.sidebar.subheader("Build")
    st.sidebar.caption(f"{usable:,} usable recipes; {dropped:,} dropped (no ingredient list). Step rule {meta().get('step_rule', '')}, map {meta().get('map_version', '')}.")


def main() -> None:
    st.set_page_config(page_title="nosh", page_icon=None)
    session = _session()
    _sidebar(session)
    st.title("nosh: a grounded recipe chatbot")
    try:
        check_ready()
    except LLMUnavailable as exc:
        st.warning(f"Chat needs an LLM: {exc}")
        return
    for role, text in st.session_state.messages:
        with st.chat_message(role):
            st.markdown(text)
    if prompt := st.chat_input("Ask about a dish, list what you have, or ask to adapt a recipe"):
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"), st.spinner("Working..."):
            try:
                reply = turn(prompt, session)
            except Exception as exc:  # degrade to a message, never a stack trace
                reply = f"Something went wrong ({type(exc).__name__}): {exc}"
            st.markdown(reply)
        st.session_state.messages.append(("assistant", reply))
        st.rerun()


main()
