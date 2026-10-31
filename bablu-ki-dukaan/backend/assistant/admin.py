from django.contrib import admin

from . import models

for m in [models.ChatSession, models.ChatMessage, models.ActionGroup,
          models.CustomTable, models.CustomField, models.CustomRecord]:
    admin.site.register(m)
