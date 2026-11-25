"""Limits shared by storage, methods and tool descriptions."""

#: The most engrams written in one call.
MAX_BATCH = 20

#: File name column width in characters; changing it needs a migration.
FILENAME_CHARS = 512

#: Content type column width in characters; changing it needs a migration.
MIME_CHARS = 255

#: The shortest signing secret for session cookies and staged tokens.
MIN_SECRET_LENGTH = 32
