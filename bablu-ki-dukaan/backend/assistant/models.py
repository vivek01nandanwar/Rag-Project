from django.conf import settings
from django.db import models

from store.models import CodedModel, next_code


class ChatSession(CodedModel):
    ID_PREFIX = "CHT"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=80, default="New chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class ActionGroup(CodedModel):
    """What the AI *proposed*. Nothing is written to the shop data until the owner presses Confirm."""

    ID_PREFIX = "ACT"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        SUPERSEDED = "superseded", "Replaced by a newer proposal"
        FAILED = "failed", "Failed"

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="action_groups")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="action_groups")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    actions = models.JSONField(default=list)   # [{tool, args, summary, danger}]
    result = models.JSONField(default=list)    # messages after confirm, or the error
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ChatMessage(CodedModel):
    ID_PREFIX = "MSG"

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10)  # "user" | "assistant"
    text = models.TextField()
    attachments = models.JSONField(default=list)  # [{name, kind}]
    group = models.ForeignKey(ActionGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


# ---------------------------------------------------------------------------
# "Extra registers": new record types the owner asks for later (Employees, Expenses, Rent...).
# They are stored as metadata + JSON, so adding one NEVER touches the core shop tables
# and needs no database migration.
# ---------------------------------------------------------------------------
class CustomTable(CodedModel):
    ID_PREFIX = "TBL"

    name = models.CharField(max_length=40, unique=True)
    code = models.CharField(max_length=6, unique=True)  # prefix for record ids, e.g. EMP -> EMP-0001
    description = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class CustomField(CodedModel):
    ID_PREFIX = "FLD"

    class Type(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        YES_NO = "yes_no", "Yes / No"
        CHOICE = "choice", "One of a list"

    table = models.ForeignKey(CustomTable, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField(max_length=40)
    field_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEXT)
    required = models.BooleanField(default=False)
    choices = models.JSONField(default=list, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]


class CustomRecord(CodedModel):
    ID_PREFIX = "REC"  # replaced by the table's own code in save()

    table = models.ForeignKey(CustomTable, on_delete=models.CASCADE, related_name="records")
    data = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = next_code(self.table.code)
        super().save(*args, **kwargs)
