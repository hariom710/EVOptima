from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme


def _safe_next(request, default: str = "/home/") -> str:
    """Resolve the post-login target, rejecting anything off this host.

    ``next`` is attacker-controlled (it arrives in the query string of a link
    to the login page). Passing it straight to ``redirect()`` turned the login
    form into an open redirect: submitting ``?next=https://evil.example.com``
    sent the freshly-authenticated user there, which is exactly the shape a
    credential-phishing follow-up wants. Only same-host absolute paths survive.
    """
    candidate = request.POST.get("next") or request.GET.get("next") or default
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # Prefer POST next (hidden field), fallback to GET,next, then /home/.
            # Both are validated against the request host -- see _safe_next().
            return redirect(_safe_next(request))
        else:
            messages.error(request, 'Invalid username or password')
    else:
        form = AuthenticationForm(request)
    return render(request, 'accounts/login.html', {'form': form})


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('/')
        else:
            messages.error(request, 'Please correct the errors below')
    else:
        form = UserCreationForm()
    return render(request, 'accounts/register.html', {'form': form})


def root_redirect(request):
    # If user is authenticated, go to home, else go to login
    if request.user.is_authenticated:
        return redirect('/home/')
    return redirect('/accounts/login/?next=/home/')


def logout_view(request):
    """Log out the user on GET and redirect to login."""
    if request.method in ['GET', 'POST']:
        logout(request)
        return redirect('/accounts/login/')
    # Fallback, treat others like GET
    logout(request)
    return redirect('/accounts/login/')
