

from django.db import models

from accounts.models.Tbl_Users import Tbl_Users
from .Tbl_Reservation import Tbl_Reservation


class Tbl_Idempotency_Key(models.Model):

    key = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(Tbl_Users, on_delete=models.CASCADE, related_name='idempotency_keys')
    request_hash = models.CharField(max_length=64)
    reservation = models.ForeignKey(Tbl_Reservation, null=True, blank=True, on_delete=models.SET_NULL, related_name='idempotency_keys')
    response_body = models.JSONField(null=True, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        db_table = 'tbl_idempotency_key'