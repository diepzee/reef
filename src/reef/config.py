import os
from functools import lru_cache

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: What settings are spelled with now.
PREFIX = "REEF_"

#: What they were spelled with when the module was called reef. Kept working
#: because twelve of them are set on Railway production -- the signing key
#: and the open-door pair among them -- and a variable renamed in code but
#: not in the deployment does not read as "renamed", it reads as absent.
#: reef treats absent config as a reason to refuse to boot, correctly, so
#: the rename has to land in code before the environment, not with it.
LEGACY_PREFIX = "RIF_"


def legacy_environment_names() -> list[str]:
    """Return the ``RIF_``-prefixed variables still doing work, sorted.

    Empty means every setting arrives under the new name and the fallback
    below can be deleted. That is the evidence for removing it; without this
    the decision is a guess about what Railway holds.

    :returns: legacy variable names with no ``REEF_`` equivalent set
    """
    return sorted(
        name
        for name in os.environ
        if name.startswith(LEGACY_PREFIX)
        and f"{PREFIX}{name[len(LEGACY_PREFIX) :]}" not in os.environ
    )


def adopt_legacy_environment() -> None:
    """Copy any legacy variable onto its new name, where the new one is unset.

    Done before :class:`Settings` reads the environment, each time the
    settings are built, because the groups read ``REEF_`` names only and
    the alternative is declaring a second alias on every field. It writes
    into the process environment, which tests must restore.
    """
    for name in legacy_environment_names():
        os.environ[f"{PREFIX}{name[len(LEGACY_PREFIX) :]}"] = os.environ[name]


def _group(prefix: str) -> SettingsConfigDict:
    """Return the configuration of one group of settings.

    :param prefix: what the group's variable names start with
    :returns: the pydantic-settings configuration
    """
    return SettingsConfigDict(env_prefix=prefix)


class DatabaseSettings(BaseSettings):
    """Where the data is."""

    model_config = _group(f"{PREFIX}DATABASE_")

    url: str = Field(
        "postgresql://reef:reef@localhost:5433/reef",
        description="The database, when Railway's DATABASE_URL is not set.",
    )
    test_url: str = Field(
        "postgresql://reef:reef@localhost:5433/reef_test",
        validation_alias=f"{PREFIX}TEST_DATABASE_URL",
        description="The database the test suite empties and fills.",
    )
    migration_url: str = Field(
        "",
        validation_alias=f"{PREFIX}MIGRATION_DATABASE_URL",
        description=(
            "A role that may change the schema. Empty: the app's own role. "
            "See Settings.migration_dsn."
        ),
    )


class EngramSettings(BaseSettings):
    """How big engrams and what an assistant loads may be."""

    model_config = _group(PREFIX)

    context_char_budget: int = Field(
        150_000, ge=1_000, description="The most text one context load returns."
    )
    max_chars: int = Field(
        200_000,
        ge=1_000,
        # The first choice is the name; the second is what it was called
        # before engrams, still set on deployments. The first wins.
        validation_alias=AliasChoices(
            f"{PREFIX}ENGRAM_MAX_CHARS", f"{PREFIX}PAGE_MAX_CHARS"
        ),
        description=(
            "The longest engram. Above the context budget on purpose: an engram "
            "bigger than the whole budget cannot be loaded in one piece "
            "anyway, so this refuses only what was never usable memory."
        ),
    )


class FileSettings(BaseSettings):
    """Stored files and the bucket that holds them."""

    model_config = _group(f"{PREFIX}S3_")

    endpoint: str = Field("", description="The S3-compatible bucket's address.")
    bucket: str = Field("", description="The bucket's name.")
    access_key: str = Field("", description="The bucket's access key. Secret.")
    secret_key: str = Field("", description="The bucket's secret key. Secret.")
    max_bytes: int = Field(
        25_000_000,
        ge=1,
        validation_alias=f"{PREFIX}FILE_MAX_BYTES",
        description="The largest file that may be stored.",
    )
    signed_url_ttl_seconds: int = Field(
        300,
        ge=1,
        le=86_400,
        validation_alias=f"{PREFIX}SIGNED_URL_TTL_SECONDS",
        description="How long a link to a stored file works.",
    )


class WebSettings(BaseSettings):
    """The site and the web app."""

    model_config = _group(PREFIX)

    base_url: str = Field(
        "", description="Where this reef is reached, without a trailing slash."
    )
    session_secret: str = Field(
        "", description="Seals the web app's session cookie. Secret."
    )
    static_dir: str = Field("frontend/dist", description="The built web app.")
    site_dir: str = Field("site", description="The public pages.")
    glama_maintainer_email: str = Field(
        "",
        description=(
            "Claims this deployment's Glama listing at "
            "/.well-known/glama.json. Unset: the route answers 404, the "
            "honest answer for a deployment nobody has claimed."
        ),
    )


class DoorSettings(BaseSettings):
    """The launch exception: sign-up without an invitation, for a while.

    Both default to closed, and both must be set for the door to admit
    anybody. See :mod:`reef.opendoor` for why it fails closed on either one
    alone.
    """

    model_config = _group(f"{PREFIX}OPEN_")

    seats: int = Field(0, ge=0, description="How many people may still come in.")
    until: str = Field("", description="When the door closes, as an ISO date.")


class AuthSettings(BaseSettings):
    """Sign-in, and reef as an OAuth server for assistants.

    See docs/runbook.md, "reef as authorization server". The OAuth proxy
    settings are read only when the WorkOS MCP client id and secret select
    the proxy in :func:`reef.server._build_auth`.
    """

    model_config = _group(PREFIX)

    workos_client_id: str = Field(
        "", validation_alias="WORKOS_CLIENT_ID", description="WorkOS sign-in."
    )
    workos_authkit_domain: str = Field(
        "",
        validation_alias="WORKOS_AUTHKIT_DOMAIN",
        description="The WorkOS AuthKit domain.",
    )
    workos_mcp_client_id: str = Field(
        "",
        validation_alias="WORKOS_MCP_CLIENT_ID",
        description="The WorkOS client assistants sign in through.",
    )
    workos_mcp_client_secret: str = Field(
        "",
        validation_alias="WORKOS_MCP_CLIENT_SECRET",
        description="Its secret. Secret.",
    )
    jwt_signing_key: str = Field(
        "",
        description=(
            "Signs reef-issued tokens and keys the OAuth store's encryption. "
            "Secret. Explicit, never derived from the WorkOS client secret: "
            "a rotation of that would silently invalidate every token."
        ),
    )
    oauth_store_dir: str = Field(
        "", description="Where OAuth state lives. Production: the volume."
    )
    allowed_client_redirects: str = Field(
        "",
        description=(
            "Comma-separated redirect patterns assistants may register. "
            "Empty: the default allowlist in reef.server."
        ),
    )


class DevSettings(BaseSettings):
    """Escape hatches for local development. Never set in production.

    Read as strictly as before they were settings: the exact upper-case name
    and, for the switch, the exact value ``1``. Other groups match names in
    any case, as pydantic-settings does; this one opens an authentication
    bypass, so ``reef_dev_insecure`` or `` 1`` must not.
    """

    model_config = SettingsConfigDict(case_sensitive=True)

    insecure: bool = Field(
        False,
        validation_alias=f"{PREFIX}DEV_INSECURE",
        description=(
            "Admit an HTTP connection without a token, as the dev person. "
            "On only when set to exactly 1."
        ),
    )

    @field_validator("insecure", mode="before")
    @classmethod
    def _exactly_one(cls, value: object) -> bool:
        """Switch the escape hatch on for ``1`` and nothing else.

        It was read that way before it became a setting. ``true`` or ``yes``
        must not open it now where they did not before.

        :param value: the raw value
        :returns: whether the hatch is open
        """
        return value is True or value == "1"

    principal_email: str = Field(
        "",
        validation_alias=f"{PREFIX}DEV_PRINCIPAL_EMAIL",
        description="Who a connection without a token is, in development.",
    )


class WorkerSettings(BaseSettings):
    """The background worker."""

    model_config = _group(f"{PREFIX}WORKER_")

    poll_seconds: float = Field(
        5.0, gt=0, description="How long it waits before looking again when idle."
    )


class SearchSettings(BaseSettings):
    """How search ranks. See :mod:`reef.search`.

    The numbers marked measured were chosen with the benchmark in
    ``scripts/eval``. Change one only with a new measurement.
    """

    model_config = _group(f"{PREFIX}SEARCH_")

    log: bool = Field(
        False,
        description=(
            "Log searches for 90 days. Off unless switched on: the privacy "
            "page promises that people are told first."
        ),
    )
    max_results: int = Field(
        50, ge=1, le=200, description="The most results one search returns."
    )
    fusion_depth: int = Field(
        50,
        ge=1,
        le=500,
        description="How many results of each ranking take part in the fusion.",
    )
    rrf_k: int = Field(
        60, ge=1, description="The constant of reciprocal rank fusion, from the paper."
    )
    # Measured through the product code on 19 November 2026, on the benchmark
    # in scripts/eval and on a private set of real engrams. Share of questions
    # with a right engram in the first five results:
    #
    #   weight     benchmark  private
    #   keywords   37.5%      80.0%
    #   1          47.0%      92.5%
    #   2          46.8%      100%
    #   3          46.0%      100%
    semantic_weight: float = Field(
        2.0,
        ge=0,
        description=(
            "Measured. How much a place in the meaning ranking counts, against "
            "1 for a place in the keyword ranking."
        ),
    )
    # Somebody who types an identifier or a name wants the engram that has
    # it. In a whole question, holding every word says little: treating
    # those engrams as sure as well found 44.0% of benchmark questions, against
    # 46.0% without (19 November 2026).
    sure_words: int = Field(
        3,
        ge=0,
        description=(
            "Measured. The longest query in which an engram holding every word "
            "keeps its keyword place in the fusion."
        ),
    )


class EmbeddingSettings(BaseSettings):
    """Vectors of engram pieces, for search by meaning. See :mod:`reef.embedding`."""

    model_config = _group(f"{PREFIX}EMBEDDING_")

    url: str = Field(
        "",
        description=(
            "Where the embedding service is. Empty: none, nothing is embedded "
            "or queued. Engram text goes there, so it must be a service this "
            "deployment runs itself, on a private network."
        ),
    )
    model: str = Field(
        "embeddinggemma:300m",
        description=(
            "Which model it serves. Stored with every vector: a change of "
            "model re-embeds everything. Must match on app and worker."
        ),
    )
    daily_cap: int = Field(
        2000, ge=0, description="The most pieces embedded for one person in one day."
    )
    delay_seconds: float = Field(
        120.0,
        ge=0,
        description="How long after a save an engram is embedded, so five edits embed once.",
    )
    seconds: float = Field(
        2.0,
        gt=0,
        description="How long a search waits for its query's vector, then uses keywords.",
    )
    batch: int = Field(
        16, ge=1, le=256, description="How many pieces go in one request."
    )
    page_seconds: float = Field(
        300.0,
        gt=0,
        description="The longest one engram's embedding may take. The job holds a connection.",
    )
    cap_retry_seconds: float = Field(
        6 * 3600.0,
        gt=0,
        description="How long an engram waits when the cap is reached.",
    )
    failure_retry_seconds: float = Field(
        30 * 60.0, gt=0, description="How long an engram waits after a failure."
    )
    question_chars: int = Field(
        500, ge=1, description="How much of a query is embedded, in characters."
    )
    rest_after_failures: int = Field(
        3,
        ge=1,
        description="Failures in a row before searches leave the service alone.",
    )
    rest_seconds: float = Field(60.0, ge=0, description="How long they leave it alone.")
    piece_min_chars: int = Field(
        200,
        ge=0,
        description="Measured. A section shorter than this joins the one before it. "
        "A change reaches an engram when it is next saved. Same value on app and worker.",
    )
    piece_max_chars: int = Field(
        2000,
        ge=100,
        description="Measured. A section longer than this is cut. "
        "A change reaches an engram when it is next saved. Same value on app and worker.",
    )
    page_chars: int = Field(
        8000,
        ge=100,
        description="How much of a long engram the whole-engram piece holds. "
        "A change reaches an engram when it is next saved. Same value on app and worker.",
    )


class RerankSettings(BaseSettings):
    """Reading the first results again. See :mod:`reef.rerank`."""

    model_config = _group(f"{PREFIX}RERANK_")

    url: str = Field(
        "",
        description=(
            "Where the reranker is. Empty: none. The query and passages of "
            "engrams the person can read go there, so it must be a service "
            "this deployment runs itself."
        ),
    )
    model: str = Field("BAAI/bge-reranker-v2-m3", description="Which model it serves.")
    enabled: bool = Field(
        True, description="The off switch. On: the reranker runs when there is one."
    )
    candidates: int = Field(
        15,
        ge=2,
        le=50,
        description="Measured. How many of the first results are read again.",
    )
    passages: int = Field(
        2, ge=1, le=10, description="Measured. How many pieces of each engram are read."
    )
    seconds: float = Field(
        3.0, gt=0, description="How long a search waits for it, then keeps its order."
    )
    query_chars: int = Field(500, ge=1, description="How much of the query is sent.")
    passage_chars: int = Field(
        2000, ge=1, description="How much of one passage is sent, in characters."
    )
    rest_after_failures: int = Field(
        3,
        ge=1,
        description="Failures in a row before searches leave the service alone.",
    )
    rest_seconds: float = Field(60.0, ge=0, description="How long they leave it alone.")
    # A judgement, not a measurement (19 November 2026): hybrid search found
    # a right engram in the first five for 95% of real questions over 37
    # engrams, and for 47% of benchmark questions over 18,000. Nothing was
    # measured in between. Adjust it when the search log shows misses rising.
    min_engrams: int = Field(
        1000,
        ge=0,
        validation_alias=AliasChoices(
            f"{PREFIX}RERANK_MIN_ENGRAMS", f"{PREFIX}RERANK_MIN_PAGES"
        ),
        description=(
            "Rerank only a search over at least this many engrams. A small memory "
            "is found well without it and skips its wait. 0 reranks every search."
        ),
    )


class UsageSettings(BaseSettings):
    """How use makes an engram stand out. See :mod:`reef.usage`."""

    model_config = _group(f"{PREFIX}USAGE_")

    decay: float = Field(
        0.5, gt=0, description="How fast a use fades. 0.5 is the value ACT-R has used."
    )
    window: int = Field(
        50, ge=1, description="How many of an engram's most recent uses count."
    )
    min_age_hours: float = Field(
        1 / 60, gt=0, description="The youngest a use is treated as, so none dominates."
    )
    refresh_delay_seconds: float = Field(
        5 * 60.0,
        ge=0,
        description="How long after a use the refresh runs, once per busy session.",
    )


class Settings(BaseModel):
    """Every setting of reef, grouped by what it is for.

    Each group reads its own environment variables, named as before the
    grouping, so nothing set on a deployment had to be renamed. This class
    reads none itself: a stray variable named like a group cannot stop the
    app from starting.

    A setting may change behaviour or cost. What would weaken a guarantee,
    or needs a migration to change, stays a constant in its module: the
    search log's 90 days and how often they are enforced, the invitation
    budget, the job lease, the longest vector a table takes.
    """

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    engrams: EngramSettings = Field(default_factory=EngramSettings)
    files: FileSettings = Field(default_factory=FileSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    door: DoorSettings = Field(default_factory=DoorSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    dev: DevSettings = Field(default_factory=DevSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    usage: UsageSettings = Field(default_factory=UsageSettings)

    @property
    def dsn(self) -> str:
        """Return the connection DSN, preferring Railway's injected value.

        Railway injects ``DATABASE_URL`` as ``postgresql://``, which is what
        asyncpg -- and therefore Piccolo -- wants unmodified.

        :returns: a DSN usable by asyncpg
        """
        return os.environ.get("DATABASE_URL", self.database.url)

    @property
    def migration_dsn(self) -> str:
        """Return the DSN schema migrations should run under.

        The app's own role must not be able to run DDL: it is the
        RLS-constrained principal, and a role that can ``ALTER TABLE`` can
        also ``DROP POLICY``. Migrations therefore need a separate, more
        privileged credential, supplied as ``REEF_MIGRATION_DATABASE_URL``.

        Falls back to :attr:`dsn` when unset, which keeps local development
        and the test suite working unchanged -- there the app role owns its
        own database and no split is needed.

        :returns: a DSN usable by asyncpg for DDL
        """
        return self.database.migration_url or self.dsn


def variables() -> list[tuple[str, str, object, str]]:
    """List every environment variable reef reads, by group.

    :returns: group name, variable name, default, description
    """
    found = []
    for group, field in Settings.model_fields.items():
        model = field.default_factory
        prefix = model.model_config["env_prefix"]
        for name, info in model.model_fields.items():
            alias = info.validation_alias
            if isinstance(alias, AliasChoices):
                # The first choice is the name; the rest are older spellings.
                alias = alias.choices[0]
            variable = alias if isinstance(alias, str) else f"{prefix}{name}".upper()
            found.append((group, variable, info.default, info.description or ""))
    return found


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    :returns: cached settings instance
    """
    adopt_legacy_environment()
    return Settings()
