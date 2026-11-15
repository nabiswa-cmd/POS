"""Single entry point for writing audit entries."""


def record(action, *, actor=None, approver=None, object_type="", object_ref="", previous=None,
           new=None, reason="", ip=None, till_number=None):
    from .models import AuditLog

    return AuditLog.objects.create(
        action=action,
        actor=actor,
        approver=approver,
        object_type=object_type,
        object_ref=str(object_ref or ""),
        previous_value=previous,
        new_value=new,
        reason=reason or "",
        ip_address=ip or None,
        till_number=till_number,
    )
