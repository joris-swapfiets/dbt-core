from dbt.adapters.factory import adapter_management, register_adapter
from dbt.flags import set_flags
from dbt.cli.flags import Flags
from dbt.config import RuntimeConfig
from dbt.config.runtime import load_project, load_profile
from dbt.events.functions import setup_event_logger
from dbt.exceptions import DbtProjectError
from dbt.parser.manifest import ManifestLoader, write_manifest
from dbt.profiler import profiler
from dbt.tracking import initialize_from_flags, track_run

from click import Context
from functools import update_wrapper


def preflight(func):
    def wrapper(*args, **kwargs):
        ctx = args[0]
        assert isinstance(ctx, Context)
        ctx.obj = ctx.obj or {}

        # Flags
        flags = Flags(ctx)
        ctx.obj["flags"] = flags
        set_flags(flags)

        # Tracking
        initialize_from_flags(flags.ANONYMOUS_USAGE_STATS, flags.PROFILES_DIR)
        ctx.with_resource(track_run(run_command=flags.WHICH))

        # Logging
        # N.B. Legacy logger is not supported
        setup_event_logger(
            flags.LOG_PATH,
            flags.LOG_FORMAT,
            flags.USE_COLORS,
            flags.DEBUG,
        )

        # Profiling
        if flags.RECORD_TIMING_INFO:
            ctx.with_resource(profiler(enable=True, outfile=flags.RECORD_TIMING_INFO))

        # Adapter management
        ctx.with_resource(adapter_management())

        return func(*args, **kwargs)

    return update_wrapper(wrapper, func)


def profile(func):
    def wrapper(*args, **kwargs):
        ctx = args[0]
        assert isinstance(ctx, Context)

        if ctx.obj.get("profile") is None:
            flags = ctx.obj["flags"]
            profile = load_profile(
                flags.PROJECT_DIR, flags.VARS, flags.PROFILE, flags.TARGET, flags.THREADS
            )
            ctx.obj["profile"] = profile

        return func(*args, **kwargs)

    return update_wrapper(wrapper, func)


def project(func):
    def wrapper(*args, **kwargs):
        ctx = args[0]
        assert isinstance(ctx, Context)

        if ctx.obj.get("project") is None:
            # TODO: Decouple target from profile, and remove the need for profile here:
            # https://github.com/dbt-labs/dbt-core/issues/6257
            if not ctx.obj.get("profile"):
                raise DbtProjectError("profile required for project")

            flags = ctx.obj["flags"]
            project = load_project(
                flags.PROJECT_DIR, flags.VERSION_CHECK, ctx.obj["profile"], flags.VARS
            )
            ctx.obj["project"] = project

        return func(*args, **kwargs)

    return update_wrapper(wrapper, func)


def runtime_config(func):
    """A decorator used by click command functions for generating a runtime
    config given a profile and project.
    """

    def wrapper(*args, **kwargs):
        ctx = args[0]
        assert isinstance(ctx, Context)

        req_strs = ["profile", "project"]
        reqs = [ctx.obj.get(req_str) for req_str in req_strs]

        if None in reqs:
            raise DbtProjectError("profile and project required for runtime_config")

        ctx.obj["runtime_config"] = RuntimeConfig.from_parts(
            ctx.obj["project"],
            ctx.obj["profile"],
            ctx.obj["flags"],
        )

        return func(*args, **kwargs)

    return update_wrapper(wrapper, func)


def manifest(*args0, write_perf_info=False):
    """A decorator used by click command functions for generating a manifest
    given a profile, project, and runtime config. This also registers the adaper
    from the runtime config and writes the manifest to disc.
    """

    def outer_wrapper(func):
        def wrapper(*args, **kwargs):
            ctx = args[0]
            assert isinstance(ctx, Context)

            req_strs = ["profile", "project", "runtime_config"]
            reqs = [ctx.obj.get(dep) for dep in req_strs]

            if None in reqs:
                raise DbtProjectError("profile, project, and runtime_config required for manifest")

            runtime_config = ctx.obj["runtime_config"]
            register_adapter(runtime_config)

            # a manifest has already been set on the context, so don't overwrite it
            if ctx.obj.get("manifest") is None:
                manifest = ManifestLoader.get_full_manifest(
                    runtime_config, write_perf_info=write_perf_info
                )

                ctx.obj["manifest"] = manifest
                if ctx.obj["flags"].write_json:
                    write_manifest(manifest, ctx.obj["runtime_config"].target_path)

            return func(*args, **kwargs)

        return update_wrapper(wrapper, func)

    # if there are no args, the decorator was used without params @decorator
    # otherwise, the decorator was called with params @decorator(arg)
    if len(args0) == 0:
        return outer_wrapper
    return outer_wrapper(args0[0])