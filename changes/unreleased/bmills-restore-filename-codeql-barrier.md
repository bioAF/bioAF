### Internal

- Add an explicit `os.path.basename` barrier after the restore-filename regex check so CodeQL's path-injection taint tracker recognises the sanitization. No behavior change for any user.
