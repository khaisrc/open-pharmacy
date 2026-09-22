"""The admin as one more adapter over the services.

A ModelAdmin here reads through its repository and writes through its
service, exactly as a viewset does, so an edit made in the admin is audited,
numbered and refused by the same code as one made through the API. A
refusal a service raises (a 409 or a 400 in API terms) becomes a message and
a redirect back to the page rather than a 500.
"""

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from rest_framework.exceptions import ValidationError

from pharmacy.exceptions import ApiError
from pharmacy.services import WriteContext


def describe(exc):
    if isinstance(exc, ApiError):
        details = {k: v for k, v in exc.details.items() if k != "status"}
        suffix = f" ({', '.join(f'{k}: {v}' for k, v in details.items())})" if details else ""
        return f"{exc.detail}{suffix}"
    detail = exc.detail
    if isinstance(detail, dict):
        return "; ".join(
            f"{field}: {' '.join(map(str, errors))}" for field, errors in detail.items()
        )
    return " ".join(map(str, detail))


class ServiceModelAdmin(admin.ModelAdmin):
    repository = None
    service = None

    def ctx(self, request):
        # No staff member: the admin identifies a login, and the change
        # history records it as such.
        return WriteContext(
            user=request.user,
            path=request.path,
            method=request.method,
            request_id=getattr(request, "request_id", ""),
        )

    def get_queryset(self, request):
        queryset = self.repository.live()
        ordering = self.get_ordering(request)
        return queryset.order_by(*ordering) if ordering else queryset

    def save_model(self, request, obj, form, change):
        if change:
            # A fresh instance, not the form's: the service snapshots it
            # before applying the change, which is how the history gets an
            # old value.
            instance = self.repository.get(obj.pk)
            changed = {name: form.cleaned_data[name] for name in form.changed_data}
            self.service.update(self.ctx(request), instance, **changed)
        else:
            # The form's pk, so the admin's own redirect and log entry find
            # the row the service created.
            self.service.create(self.ctx(request), id=obj.pk, **form.cleaned_data)

    def delete_model(self, request, obj):
        self.service.delete(self.ctx(request), obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.service.delete(self.ctx(request), obj)

    # The three entry points a write goes through. A service refusal has
    # already rolled back; show it and return to the page.
    def _guarded(self, request, view, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except (ApiError, ValidationError) as exc:
            messages.error(request, describe(exc))
            return HttpResponseRedirect(request.path)

    def changeform_view(self, request, *args, **kwargs):
        return self._guarded(request, super().changeform_view, *args, **kwargs)

    def delete_view(self, request, *args, **kwargs):
        return self._guarded(request, super().delete_view, *args, **kwargs)

    def changelist_view(self, request, *args, **kwargs):
        return self._guarded(request, super().changelist_view, *args, **kwargs)


class ReadOnlyAdminMixin:
    """A record that is produced, never authored: the history, and the
    fills the workflow drives."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.concrete_fields]
