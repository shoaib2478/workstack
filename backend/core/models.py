import uuid
from django.db import models

class BaseTimeStampedModel(models.Model):
    uuid = models.UUIDField(
        default=uuid.uuid4, 
        editable=False, 
        unique=True, 
        db_index=True,       
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True