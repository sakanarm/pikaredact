import os

# The app tests run with sign-in off (one local admin); tests/test_auth.py turns it on for itself.
os.environ["AUTH_MODE"] = "off"
