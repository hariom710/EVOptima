from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required


def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # Prefer POST next (hidden field), fallback to GET,next, then /home/
            next_url = request.POST.get('next') or request.GET.get('next') or '/home/'
            return redirect(next_url)
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
