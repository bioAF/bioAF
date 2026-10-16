### Installers

- Install bioAF from a new Mac with nothing on it. `install-gcp.sh` and
  `install-aws.sh` now install Homebrew when it is missing (it asks for the Mac
  password once), then gcloud or the AWS CLI, and carry on in the same run
  instead of stopping and asking the user to open a new terminal.
- Run gcloud on the Python its Homebrew package depends on, checked rather than
  assumed, so a Mac whose built-in Python is too old for gcloud no longer fails
  partway through. An existing gcloud on an unsupported Python is caught before
  sign-in, with a plain explanation.

### Fixes

- Make the documented `curl ... | bash` one-liner work. Every prompt used to read
  the next line of the script instead of the keyboard, so the first question
  always ended in "Cancelled." Prompts, the Mac password, Google sign-in, and
  SSH now all read from the terminal, and the installer says so plainly when it
  is not run in one.
