"""The assistant as a LangGraph state machine. Every path ends at `finalize`; nothing can loop forever.

START ─(files?)─► ingest ─► guard ─(blocked?)─► refuse ───────────────┐
  └────(no files)──────────► guard ─(ok)─► route ─(chat)─► chat ──────┤
                                             └(shop/custom)─► agent ◄─┐│
                                                   ├─(tool calls)─► tools ┘│
                                                   └─(final answer)────────┴─► finalize ─► END
"""
import logging
import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from . import ingest, llm, prompts, toolkit, vector
from .models import CustomTable

log = logging.getLogger("assistant")
MAX_STEPS = 6   # agent <-> tools round trips before we force a summary

DESTRUCTIVE = re.compile(
    r"(drop\s+(table|database)|truncate|delete\s+(all|everything)|wipe\s+(all|everything|the)|erase\s+(all|everything)|"
    r"rm\s+-rf|sab\s+(kuch\s+)?(delete|mita)|saara?\s+data\s+(delete|mita)|;\s*(delete|drop|update)\s)", re.I)
SECRETS = re.compile(
    r"(api[\s_-]?key|secret[\s_-]?key|system\s+prompt|your\s+instructions|ignore\s+(all\s+|any\s+|the\s+)?(previous|above|prior)\s+(instructions|rules)|"
    r"show\s+.{0,20}password|\.env\b|reveal\s+.{0,20}(prompt|key))", re.I)

SHOP_HINT = re.compile(
    r"(price|rate|bhav|bhaav|kimat|keemat|kitne|kitna|cost|profit|stock|baaki|baki|udhaar|udhar|bikri|becha|bechi|khareed|kharid|"
    r"purchase|sale|sold|bought|return|wapas|bill|order|supplier|customer|ledger|₹|rs\.?\s*\d|\bkg\b|\bgram|\bgm\b|litre|packet|dozen)", re.I)

REFUSE_DESTRUCTIVE = ("🙅 I can't wipe or bulk-delete shop data - it would destroy your bills, stock and udhaar records.\n\n"
                      "Safe alternatives: delete ONE unused product/customer, hide a product, recount stock, "
                      "or tell me exactly which record is wrong and I'll suggest a correction.")
REFUSE_SECRET = "🔒 I can't share or change keys, passwords or my own instructions. Anything about your shop - stock, bills, udhaar - I'm happy to help with!"


class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    user_text: str
    attachments: list
    notes: list
    intent: str
    steps: int
    refusal: str
    reply: str


def build_graph(ctx):
    tools = toolkit.build(ctx)

    def tools_for(intent):
        return [tools[n] for n in toolkit.TOOLSETS.get(intent, toolkit.TOOLSETS["shop"])]

    # ------------------------------------------------------------- nodes
    def n_ingest(state):
        results, notes = [], []
        for f in ctx.files:
            try:
                r = ingest.process_file(f)
                results.append(r)
                if r["text"]:
                    vector.add_document(ctx.session.id, r["name"], r["text"])
            except Exception as e:
                log.warning("ingest failed for %s: %s", f.name, e)
                notes.append(f"(The file '{f.name}' could not be read: {type(e).__name__}. Ask the owner to try another format.)")
        return {"attachments": results, "notes": notes}

    def n_guard(state):
        text, atts = state["user_text"], state.get("attachments", [])
        if not text.strip() and not atts:
            return {"refusal": "Please type a message or attach a file 🙂"}
        if len(text) > 2000:
            return {"refusal": "That message is too long for me. Please send it in smaller parts (max 2000 characters)."}
        if DESTRUCTIVE.search(text):
            return {"refusal": REFUSE_DESTRUCTIVE}
        if SECRETS.search(text):
            return {"refusal": REFUSE_SECRET}
        full = text.strip()
        if atts:
            full += ("\n\n" if full else "") + "[Files attached by the owner]\n" + ingest.attachment_block(atts)
        if state.get("notes"):
            full += "\n\n" + "\n".join(state["notes"])
        if not text.strip():
            full = "(The owner wrote no message, only attached files. Work out what they most likely want, or ask.)\n\n" + full
        return {"messages": [HumanMessage(content=full)]}

    def n_refuse(state):
        return {"reply": state["refusal"]}

    def n_route(state):
        text = state["user_text"]
        names = list(CustomTable.objects.values_list("name", flat=True))
        low = text.lower()
        if any(re.search(rf"\b{re.escape(n.lower())}\b", low) for n in names):
            return {"intent": "custom"}
        last_ai = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        snippets = " | ".join(f"{a['kind']}: {a['text'][:200]}" for a in state.get("attachments", []))
        payload = (f"Existing extra registers: {', '.join(names) or 'none'}\n"
                   f"Last assistant message: {llm.text_of(last_ai)[:300] if last_ai else '-'}\n"
                   f"Owner message: {text[:500] or '-'}\nFiles: {snippets or '-'}")
        try:
            resp = llm.fast_llm().invoke([SystemMessage(content=prompts.ROUTER), HumanMessage(content=payload)])
            m = re.search(r"\b(chat|shop|custom)\b", llm.text_of(resp).lower())
            intent = m.group(1) if m else "shop"
        except llm.AssistantNotConfigured:
            raise
        except Exception as e:
            log.warning("router failed, defaulting to shop: %s", e)
            intent = "shop"
        if intent == "chat" and (state.get("attachments") or SHOP_HINT.search(text)):
            intent = "shop"   # never treat a price / stock / bill question as small talk
        return {"intent": intent}

    def n_chat(state):
        try:
            resp = llm.fast_llm().invoke([SystemMessage(content=prompts.CHAT), *state["messages"][-6:]])
            return {"reply": llm.text_of(resp).strip() or "🙏 Namaste! How can I help with the dukaan today?"}
        except llm.AssistantNotConfigured:
            raise
        except Exception as e:
            log.warning("chat failed: %s", e)
            return {"reply": "🙏 Namaste! Tell me what you need for the dukaan."}

    def n_agent(state):
        steps = state.get("steps", 0)
        wrap_up = steps >= MAX_STEPS
        model = llm.agent_llm(tools_for(state["intent"]), allow_tools=not wrap_up)
        system = SystemMessage(content=prompts.system_prompt(ctx.user, state["intent"], wrap_up=wrap_up))
        try:
            resp = model.invoke([system, *state["messages"]])
        except llm.AssistantNotConfigured:
            raise
        except Exception as e:  # all models failed (rate limit, outage, malformed tool call...)
            log.error("agent failed: %s", e)
            resp = AIMessage(content="😅 My brain is busy right now (the AI service didn't answer). Please try again in a minute.")
        return {"messages": [resp], "steps": steps + 1}

    def n_tools(state):
        allowed = {t.name: t for t in tools_for(state["intent"])}   # a made-up tool name can never run
        out = []
        for call in state["messages"][-1].tool_calls:
            tool = allowed.get(call["name"])
            if tool is None:
                content = f"ERROR: tool '{call['name']}' is not available."
            else:
                try:
                    content = str(tool.invoke(call["args"]))
                except Exception as e:
                    log.warning("tool %s crashed: %s", call["name"], e)
                    content = f"ERROR: invalid arguments ({str(e)[:200]})."
            out.append(ToolMessage(content=content[:4800], tool_call_id=call["id"], name=call["name"]))
        return {"messages": out}

    def n_finalize(state):
        text = (state.get("reply") or "").strip()
        if not text:
            last = state["messages"][-1] if state.get("messages") else None
            text = llm.text_of(last).strip() if isinstance(last, AIMessage) else ""
        if not text:
            text = "I've prepared this for you." if ctx.proposals else "Sorry, I couldn't work that out. Could you say it another way?"
        if ctx.proposals and not re.search(r"confirm|✅", text, re.I):
            text += "\n\nPlease check the card below and press ✅ Confirm to save it."
        return {"reply": text}

    # ------------------------------------------------------------- conditional edges
    def e_start(state):
        return "ingest" if ctx.files else "guard"

    def e_guard(state):
        return "refuse" if state.get("refusal") else "route"

    def e_route(state):
        return "chat" if state.get("intent") == "chat" else "agent"

    def e_agent(state):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state.get("steps", 0) <= MAX_STEPS:
            return "tools"
        return "finalize"

    g = StateGraph(State)
    for name, fn in [("ingest", n_ingest), ("guard", n_guard), ("refuse", n_refuse), ("route", n_route),
                     ("chat", n_chat), ("agent", n_agent), ("tools", n_tools), ("finalize", n_finalize)]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, e_start, {"ingest": "ingest", "guard": "guard"})
    g.add_edge("ingest", "guard")
    g.add_conditional_edges("guard", e_guard, {"refuse": "refuse", "route": "route"})
    g.add_edge("refuse", "finalize")
    g.add_conditional_edges("route", e_route, {"chat": "chat", "agent": "agent"})
    g.add_edge("chat", "finalize")
    g.add_conditional_edges("agent", e_agent, {"tools": "tools", "finalize": "finalize"})
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)
    return g.compile()


def run_assistant(ctx, user_text, history):
    graph = build_graph(ctx)
    final = graph.invoke(
        {"messages": history, "user_text": user_text, "steps": 0, "intent": "shop", "attachments": [], "notes": []},
        config={"recursion_limit": 40},
    )
    return {
        "reply": final.get("reply", ""),
        "intent": final.get("intent", ""),
        "proposals": ctx.proposals,
        "read": [{"name": a["name"], "kind": a["kind"], "preview": a["text"][:240]} for a in final.get("attachments", [])],
    }
