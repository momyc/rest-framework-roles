import sys
import inspect
import importlib
import logging

from django.urls import resolve, get_resolver
from django.urls.resolvers import URLPattern
from django.conf import settings
from django.utils.functional import empty

from rest_framework_roles.permissions import check_permissions

logger = logging.getLogger(__name__)

DJANGO_CLASS_VIEWS = {
    'get',
    'post',
    'put',
    'patch',
    'delete',
    'head',
    'options',
    'trace',
}


def is_django_configured():
    return settings._wrapped is not empty


def is_rest_framework_loaded():
    return 'rest_framework' in sys.modules.keys()


# ------------------------------ Wrappers --------------------------------------


def function_view_wrapper(view):
    """ Wraps a Django view """
    def wrapped(request, *args, **kwargs):
        logger.debug('RUNNING: function_view_wrapper.wrapped()')
        check_permissions(request, view, *args, **kwargs)
        return view(request, *args, **kwargs)
    return wrapped


def class_view_wrapper(view):
    """ Wraps a Django method view """
    def wrapped(self, request, *args, **kwargs):
        logger.debug('RUNNING: class_view_wrapper.wrapped()')
        check_permissions(request, self, *args, **kwargs)  # Note we pass the class as view instead of function
        return view(self, request, *args, **kwargs)
    return wrapped


def check_permissions_wrapper(original_check_permissions):
    """ Wraps Django REST framework check_permissions method """
    def wrapped(self, request):
        logger.debug('RUNNING: check_permissions_wrapper.wrapped()')
        check_permissions(request, self)  # Note we pass the class as view instead of function
        return original_check_permissions(self, request)
    return wrapped


# ------------------------------------------------------------------------------


def is_method_view(callback):
    """
    Check if callback of pattern is a method
    """
    if hasattr(callback, 'view_class'):
        return True
    try:
        # Heurestic; all class methods end up calling the dispatch method
        return callback.__wrapped__.__wrapped__.__name__ == 'dispatch'
    except AttributeError:
        pass
    return False


def get_view_class(callback):
    """
    Try to get the class from given callback
    """
    if hasattr(callback, 'view_class'):
        return callback.view_class
    mod = importlib.import_module(callback.__module__)
    cls = getattr(mod, callback.__name__)
    return cls


def patch(urlconf=None):
    """
    Entrypoint for all patching (after configurations have loaded)

    Args:
        urlconf(str): Path to urlconf, by default using ROOT_URLCONF
    """
    # Get all active patterns
    class_patterns = []
    function_patterns = []
    for pattern in get_urlpatterns(urlconf):
        if is_method_view(pattern.callback):
            class_patterns.append(pattern)
        else:
            function_patterns.append(pattern)

    # Patch simple function views directly
    for pattern in function_patterns:
        # logger.warn(f'Patching {pattern.callback}')
        pattern.callback = function_view_wrapper(pattern.callback)

    # Patch class based methods
    for pattern in class_patterns:
        cls = get_view_class(pattern.callback)

        # Patching for Django
        if not hasattr(cls, 'check_permissions'):
            methods = set(dir(cls)) & DJANGO_CLASS_VIEWS
            # Actual patching of method
            for method_name in methods:
                original_method = getattr(cls, method_name)
                setattr(cls, method_name, class_view_wrapper(original_method))

        # Patching for Django REST Framework
        else:
            original_check_permissions = getattr(cls, 'check_permissions')
            setattr(cls, 'check_permissions', check_permissions_wrapper(original_check_permissions))


def get_urlpatterns(urlconf=None):
    if not urlconf:
        urlconf = importlib.import_module(settings.ROOT_URLCONF)
    assert type(urlconf) != str, f"URLConf should not be string. Got '{urlconf}'"
    return list(iter_urlpatterns(urlconf.urlpatterns))


def iter_urlpatterns(urlpatterns):
    for entity in urlpatterns:
        if hasattr(entity, 'url_patterns'):
            yield from iter_urlpatterns(entity.url_patterns)
        elif hasattr(entity, 'urlpatterns'):
            yield from iter_urlpatterns(entity.urlpatterns)
        else:
            assert type(entity) == URLPattern, f"Expected pattern, got '{entity}'"
            yield entity


def extract_views_from_urlpatterns(urlpatterns):
    """
    Similar to iter_urlpatterns but uses django-extensions' show_urls methodology
    """
    from django_extensions.management.commands.show_urls import Command
    return Command().extract_views_from_urlpatterns(urlpatterns)
