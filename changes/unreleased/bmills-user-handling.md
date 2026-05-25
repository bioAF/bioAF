### Password reset

- Password reset emails now contain a working reset link (valid for 60 minutes)
  alongside the 6-digit code. Following the link opens a page where you enter the
  code, set and confirm a new password, and are redirected to sign in. This
  replaces the old email that contained only a bare code with nowhere to use it.
- Add a "Forgot password?" link on the sign-in page so users can request a reset
  themselves.

### Profile

- Clicking your name in the top-right header now opens a single Profile page with
  tabs for Account, Session Credentials, Git SSH Key, and Notifications.
- The Account tab shows your email and role and lets you set your display name and
  change your password. Your name (falling back to your email) is what now shows
  in the header.
- Remove the role badge from the header, the username/role block from the bottom
  of the sidebar, and the Profile menu from the sidebar navigation.

### Fixes

- Allow deleting a deactivated user who never logged in even if an admin had sent
  them a password reset. The pending reset code no longer blocks the deletion, and
  the admin's action remains recorded in the audit log.
