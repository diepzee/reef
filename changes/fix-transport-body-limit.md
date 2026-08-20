---
kind: fixed
---
Storing a large file no longer fails: uploads up to the full 25MB file
limit now make it through, where anything over about 3MB used to be
rejected before reef saw it.
