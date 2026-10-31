import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from . import ingest, ops, vector
from .graph import run_assistant
from .llm import AssistantNotConfigured
from .models import ActionGroup, ChatMessage, ChatSession, CustomTable

log = logging.getLogger("assistant")
MAX_TEXT = 2000


# ------------------------------------------------------------------ serializers (hand-written: nothing internal leaks)
def expire_if_needed(g):
    if g.status == ActionGroup.Status.PENDING and g.expires_at < timezone.now():
        g.status = ActionGroup.Status.EXPIRED
        g.resolved_at = timezone.now()
        g.save(update_fields=["status", "resolved_at"])


def group_json(g):
    expire_if_needed(g)
    return {
        "id": g.id, "status": g.status, "expires_at": g.expires_at.isoformat(),
        "actions": [{"summary": a["summary"], "danger": a["danger"]} for a in g.actions],  # args stay on the server
        "result": g.result,
    }


def msg_json(m):
    return {
        "id": m.id, "role": m.role, "text": m.text, "attachments": m.attachments,
        "created_at": m.created_at.isoformat(), "proposal": group_json(m.group) if m.group_id else None,
    }


def build_history(session, limit=8):
    rows = list(session.messages.select_related("group").order_by("-created_at")[:limit])[::-1]
    out = []
    for m in rows:
        text = m.text[:600]
        if m.role == "user":
            out.append(HumanMessage(content=text or "(attachment)"))
        else:
            note = f"\n[proposal {m.group.status}]" if m.group_id else ""
            out.append(AIMessage(content=text + note))
    return out


# ------------------------------------------------------------------ chat
class ChatView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "chat"

    def post(self, request):
        text = str(request.data.get("message", "")).strip()
        files = request.FILES.getlist("files")
        if not text and not files:
            return Response({"detail": "Type a message or attach a file."}, status=400)
        if len(text) > MAX_TEXT:
            return Response({"detail": f"Message is too long (max {MAX_TEXT} characters)."}, status=400)
        if len(files) > settings.ASSISTANT_MAX_FILES:
            return Response({"detail": f"You can attach at most {settings.ASSISTANT_MAX_FILES} files."}, status=400)
        for f in files:
            try:
                ingest.validate_upload(f)
            except ValueError as e:
                return Response({"detail": str(e)}, status=400)

        sid = request.data.get("session")
        if sid:
            session = get_object_or_404(ChatSession, pk=sid, user=request.user)   # you can only open your own chats
        else:
            session = ChatSession.objects.create(user=request.user, title=(text or files[0].name)[:60])

        history = build_history(session)
        user_msg = ChatMessage.objects.create(
            session=session, role="user", text=text,
            attachments=[{"name": f.name, "kind": ingest.kind_of(f.name)} for f in files])

        ctx = ops.Ctx(request.user, session, files)
        try:
            result = run_assistant(ctx, text, history)
        except AssistantNotConfigured as e:
            user_msg.delete()
            return Response({"detail": f"The assistant is not set up yet: {e}."}, status=503)
        except Exception:
            log.exception("assistant crashed")
            user_msg.delete()
            return Response({"detail": "The assistant had a problem. Please try again."}, status=500)

        with transaction.atomic():
            group = None
            now = timezone.now()
            if result["proposals"]:
                ActionGroup.objects.filter(session=session, status=ActionGroup.Status.PENDING).update(
                    status=ActionGroup.Status.SUPERSEDED, resolved_at=now)
                group = ActionGroup.objects.create(
                    session=session, user=request.user, actions=result["proposals"],
                    expires_at=now + timedelta(minutes=settings.ASSISTANT_PENDING_MINUTES))
            reply = ChatMessage.objects.create(session=session, role="assistant", text=result["reply"], group=group)
            session.save(update_fields=["updated_at"])

        return Response({
            "session": session.id, "title": session.title, "user_message_id": user_msg.id,
            "message": msg_json(reply), "read": result["read"], "intent": result["intent"],
        })


# ------------------------------------------------------------------ confirm / cancel (the ONLY way data gets written)
class ConfirmView(APIView):
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "confirm"

    def post(self, request, pk):
        changed_items = False
        with transaction.atomic():
            g = ActionGroup.objects.select_for_update().filter(pk=pk, user=request.user).first()
            if g is None:
                return Response({"detail": "Not found."}, status=404)
            expire_if_needed(g)
            if g.status != ActionGroup.Status.PENDING:
                return Response({"detail": f"This request is already {g.get_status_display().lower()}."}, status=409)
            try:
                results = ops.run_group(g, request.user)
                g.status, g.result = ActionGroup.Status.CONFIRMED, results
                changed_items = any(a["tool"] in ops.ITEM_CHANGING for a in g.actions)
            except ops.ToolFail as e:   # a rule was broken: everything is rolled back
                g.status, g.result = ActionGroup.Status.FAILED, [str(e)]
            except Exception:
                log.exception("confirm failed")
                g.status, g.result = ActionGroup.Status.FAILED, ["Something went wrong, nothing was saved."]
            g.resolved_at = timezone.now()
            g.save()

            if g.status == ActionGroup.Status.CONFIRMED:
                text = "✅ Done!\n" + "\n".join(f"• {r}" for r in g.result)
            else:
                text = f"⚠️ I could not save this, so nothing was changed.\nReason: {g.result[0]}"
            msg = ChatMessage.objects.create(session=g.session, role="assistant", text=text)
            g.session.save(update_fields=["updated_at"])

        if changed_items:
            vector.reindex_items()
        return Response({"proposal": group_json(g), "message": msg_json(msg)})


class CancelView(APIView):
    def post(self, request, pk):
        with transaction.atomic():
            g = ActionGroup.objects.select_for_update().filter(pk=pk, user=request.user).first()
            if g is None:
                return Response({"detail": "Not found."}, status=404)
            expire_if_needed(g)
            if g.status != ActionGroup.Status.PENDING:
                return Response({"detail": f"This request is already {g.get_status_display().lower()}."}, status=409)
            g.status, g.resolved_at = ActionGroup.Status.CANCELLED, timezone.now()
            g.save()
            msg = ChatMessage.objects.create(session=g.session, role="assistant", text="Okay, cancelled ✋ Nothing was changed.")
        return Response({"proposal": group_json(g), "message": msg_json(msg)})


# ------------------------------------------------------------------ history
class SessionListView(APIView):
    def get(self, request):
        rows = ChatSession.objects.filter(user=request.user)[:50]
        return Response([{"id": s.id, "title": s.title, "updated_at": s.updated_at.isoformat()} for s in rows])


class SessionDetailView(APIView):
    def get(self, request, pk):
        s = get_object_or_404(ChatSession, pk=pk, user=request.user)
        msgs = s.messages.select_related("group")[:200]
        return Response({"id": s.id, "title": s.title, "messages": [msg_json(m) for m in msgs]})

    def delete(self, request, pk):
        s = get_object_or_404(ChatSession, pk=pk, user=request.user)
        vector.delete_session_docs(s.id)
        s.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StatusView(APIView):
    def get(self, request):
        return Response({
            "configured": bool(settings.GROQ_API_KEY),
            "models": {"main": settings.GROQ_MODEL_MAIN, "fast": settings.GROQ_MODEL_FAST,
                       "vision": settings.GROQ_MODEL_VISION, "audio": settings.GROQ_MODEL_WHISPER},
            "limits": {"files": settings.ASSISTANT_MAX_FILES, "mb": settings.ASSISTANT_MAX_FILE_MB},
        })


class RegistersView(APIView):
    """Read-only view of the extra registers for the 'Extra registers' tab."""

    def get(self, request):
        out = []
        for t in CustomTable.objects.prefetch_related("fields"):
            out.append({
                "id": t.id, "name": t.name, "code": t.code, "description": t.description,
                "fields": [{"name": f.name, "type": f.field_type, "required": f.required} for f in t.fields.all()],
                "records": [{"id": r.id, **r.data} for r in t.records.all()[:300]],
            })
        return Response(out)
