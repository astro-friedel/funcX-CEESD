from __future__ import annotations

import asyncio
import json
import logging
import os
import typing as t
import uuid
import warnings

from funcx.errors import (
    FuncxTaskExecutionFailed,
    SerializationError,
    TaskPending,
    handle_response_errors,
)
from funcx.sdk._environments import (
    get_web_service_url,
    get_web_socket_url,
    urls_might_mismatch,
)
from funcx.sdk.asynchronous.funcx_task import FuncXTask
from funcx.sdk.asynchronous.ws_polling_task import WebSocketPollingTask
from funcx.sdk.search import SearchHelper
from funcx.sdk.web_client import FunctionRegistrationData
from funcx.serialize import FuncXSerializer
from funcx.version import __version__, compare_versions

from .batch import Batch
from .login_manager import LoginManager, LoginManagerProtocol

logger = logging.getLogger(__name__)

_FUNCX_HOME = os.path.join("~", ".funcx")


class FuncXClient:
    """Main class for interacting with the funcX service

    Holds helper operations for performing common tasks with the funcX service.
    """

    FUNCX_SDK_CLIENT_ID = os.environ.get(
        "FUNCX_SDK_CLIENT_ID", "4cf29807-cf21-49ec-9443-ff9a3fb9f81c"
    )
    FUNCX_SCOPE = os.environ.get(
        "FUNCX_SCOPE",
        "https://auth.globus.org/scopes/facd7ccc-c5f4-42aa-916b-a0e270e2c2a9/all",
    )

    def __init__(
        self,
        http_timeout=None,
        funcx_home=_FUNCX_HOME,
        asynchronous=False,
        loop=None,
        environment: str | None = None,
        funcx_service_address: str | None = None,
        results_ws_uri: str | None = None,
        warn_about_url_mismatch: bool = True,
        task_group_id: t.Union[None, uuid.UUID, str] = None,
        do_version_check: bool = True,
        openid_authorizer: t.Any = None,
        search_authorizer: t.Any = None,
        fx_authorizer: t.Any = None,
        *,
        login_manager: LoginManagerProtocol | None = None,
        **kwargs,
    ):
        """
        Initialize the client

        Parameters
        ----------
        http_timeout: int
            Timeout for any call to service in seconds.
            Default is no timeout

        environment: str
            For internal use only. The name of the environment to use. Sets
            funcx_service_address and results_ws_uri unless they are already passed in.

        funcx_service_address: str
            For internal use only. The address of the web service.

        results_ws_uri: str
            For internal use only. The address of the websocket service.

        warn_about_url_mismatch:
            For internal use only. If true, a warning is logged if funcx_service_address
            and results_ws_uri appear to point to different environments.

        do_version_check: bool
            Set to ``False`` to skip the version compatibility check on client
            initialization
            Default: True

        asynchronous: bool
        Should the API use asynchronous interactions with the web service? Currently
        only impacts the run method
        Default: False

        loop: AbstractEventLoop
        If asynchronous mode is requested, then you can provide an optional event loop
        instance. If None, then we will access asyncio.get_event_loop()
        Default: None

        task_group_id: str|uuid.UUID
            Set the TaskGroup ID (a UUID) for this FuncXClient instance.  Typically,
            one uses this to submit new tasks to an existing session or to reestablish
            FuncXExecutor futures.
            Default: None (will be auto generated)

        Keyword arguments are the same as for BaseClient.

        """
        # resolve URLs if not set
        if funcx_service_address is None:
            funcx_service_address = get_web_service_url(environment)
        if results_ws_uri is None:
            results_ws_uri = get_web_socket_url(environment)

        if warn_about_url_mismatch and urls_might_mismatch(
            funcx_service_address, results_ws_uri
        ):
            logger.warning(
                f"funcx_service_address={funcx_service_address} and "
                f"results_ws_uri={results_ws_uri} "
                "look like they might point to different environments. double check "
                "that they are the correct URLs."
            )

        self._task_status_table: t.Dict[str, t.Dict] = {}
        self.funcx_home = os.path.expanduser(funcx_home)
        self.session_task_group_id = (
            task_group_id and str(task_group_id) or str(uuid.uuid4())
        )

        for (arg, name) in [
            (openid_authorizer, "openid_authorizer"),
            (fx_authorizer, "fx_authorizer"),
            (search_authorizer, "search_authorizer"),
        ]:
            if arg is not None:
                warnings.warn(
                    f"The '{name}' argument is deprecated. "
                    "It will be removed in a future release.",
                    DeprecationWarning,
                )

        # if a login manager was passed, no login flow is triggered
        if login_manager is not None:
            self.login_manager: LoginManagerProtocol = login_manager
        # but if login handling is implicit (as when no login manager is passed)
        # then ensure that the user is logged in
        else:
            self.login_manager = LoginManager(environment=environment)
            self.login_manager.ensure_logged_in()

        self.web_client = self.login_manager.get_funcx_web_client(
            base_url=funcx_service_address
        )
        self.fx_serializer = FuncXSerializer()

        self.funcx_service_address = funcx_service_address

        if do_version_check:
            self.version_check()

        self.results_ws_uri = results_ws_uri
        self.asynchronous = asynchronous
        if asynchronous:
            self.loop = loop if loop else asyncio.get_event_loop()

            # Start up an asynchronous polling loop in the background
            self.ws_polling_task = WebSocketPollingTask(
                self,
                self.loop,
                init_task_group_id=self.session_task_group_id,
                results_ws_uri=self.results_ws_uri,
            )
        else:
            self.loop = None

        # TODO: remove this
        self._searcher = None

    @property
    def searcher(self):
        # TODO: remove this
        if self._searcher is None:
            self._searcher = SearchHelper(self.login_manager.get_search_client())
        return self._searcher

    def version_check(self, endpoint_version: str | None = None) -> None:
        """Check this client version meets the service's minimum supported version.

        Raises a VersionMismatch error on failure.
        """
        data = self.web_client.get_version()

        min_ep_version = data["min_ep_version"]
        min_sdk_version = data["min_sdk_version"]

        compare_versions(__version__, min_sdk_version)
        if endpoint_version is not None:
            compare_versions(
                endpoint_version, min_ep_version, package_name="funcx-endpoint"
            )

    def logout(self):
        """Remove credentials from your local system"""
        self.login_manager.logout()

    def _update_task_table(self, return_msg: str | t.Dict, task_id: str):
        """
        Parses the return message from the service and updates the
        internal _task_status_table

        Parameters
        ----------

        return_msg : str | t.Dict
           Return message received from the funcx service
        task_id : str
           task id string
        """
        if isinstance(return_msg, str):
            r_dict = json.loads(return_msg)
        else:
            r_dict = return_msg

        r_status = r_dict.get("status", "unknown").lower()
        pending = r_status not in ("success", "failed")
        status = {"pending": pending, "status": r_status}

        if not pending:
            if "result" not in r_dict and "exception" not in r_dict:
                raise ValueError("non-pending result is missing result data")
            completion_t = r_dict["completion_t"]
            if "result" in r_dict:
                try:
                    r_obj = self.fx_serializer.deserialize(r_dict["result"])
                except Exception:
                    raise SerializationError("Result Object Deserialization")
                else:
                    status.update({"result": r_obj, "completion_t": completion_t})
            elif "exception" in r_dict:
                raise FuncxTaskExecutionFailed(r_dict["exception"], completion_t)
            else:
                raise NotImplementedError("unreachable")

        self._task_status_table[task_id] = status
        return status

    def get_task(self, task_id):
        """Get a funcX task.

        Parameters
        ----------
        task_id : str
            UUID of the task

        Returns
        -------
        dict
            Task block containing "status" key.
        """
        task = self._task_status_table.get(task_id, {})
        if task.get("pending", True) is False:
            return task

        r = self.web_client.get_task(task_id)
        logger.debug(f"Response string : {r}")
        rets = self._update_task_table(r.text, task_id)
        return rets

    def get_result(self, task_id):
        """Get the result of a funcX task

        Parameters
        ----------
        task_id: str
            UUID of the task

        Returns
        -------
        Result obj: If task completed

        Raises
        ------
        Exception obj: Exception due to which the task failed
        """
        task = self.get_task(task_id)
        if task["pending"] is True:
            raise TaskPending(task["status"])
        else:
            if "result" in task:
                return task["result"]
            else:
                logger.warning("We have an exception : {}".format(task["exception"]))
                task["exception"].reraise()

    def get_batch_result(self, task_id_list):
        """Request status for a batch of task_ids"""
        assert isinstance(
            task_id_list, list
        ), "get_batch_result expects a list of task ids"

        pending_task_ids = [
            task_id
            for task_id in task_id_list
            if self._task_status_table.get(task_id, {}).get("pending", True) is True
        ]

        results = {}

        if pending_task_ids:
            r = self.web_client.get_batch_status(pending_task_ids)
            logger.debug(f"Response string : {r}")

        pending_task_ids = set(pending_task_ids)

        for task_id in task_id_list:
            if task_id in pending_task_ids:
                try:
                    data = r["results"][task_id]
                    rets = self._update_task_table(data, task_id)
                    results[task_id] = rets
                except KeyError:
                    logger.debug("Task {} info was not available in the batch status")
                except Exception:
                    logger.exception(
                        "Failure while unpacking results fom get_batch_result"
                    )
            else:
                results[task_id] = self._task_status_table[task_id]

        return results

    def run(self, *args, endpoint_id=None, function_id=None, **kwargs) -> str:
        """Initiate an invocation

        Parameters
        ----------
        *args : Any
            Args as specified by the function signature
        endpoint_id : uuid str
            Endpoint UUID string. Required
        function_id : uuid str
            Function UUID string. Required
        asynchronous : bool
            Whether or not to run the function asynchronously

        Returns
        -------
        task_id : str
        UUID string that identifies the task if asynchronous is False

        funcX Task: asyncio.Task
        A future that will eventually resolve into the function's result if
        asynchronous is True
        """
        assert endpoint_id is not None, "endpoint_id key-word argument must be set"
        assert function_id is not None, "function_id key-word argument must be set"

        batch = self.create_batch()
        batch.add(*args, endpoint_id=endpoint_id, function_id=function_id, **kwargs)
        r = self.batch_run(batch)

        return r[0]

    def create_batch(self, task_group_id=None) -> Batch:
        """
        Create a Batch instance to handle batch submission in funcX

        Parameters
        ----------

        task_group_id : str
            Override the session wide session_task_group_id with a different
            task_group_id for this batch.
            If task_group_id is not specified, it will default to using the client's
            session_task_group_id

        Returns
        -------
        Batch instance
            Status block containing "status" key.
        """
        if not task_group_id:
            task_group_id = self.session_task_group_id

        return Batch(task_group_id=task_group_id)

    def batch_run(self, batch) -> t.List[str]:
        """Initiate a batch of tasks to funcX

        Parameters
        ----------
        batch: a Batch object

        Returns
        -------
        task_ids : a list of UUID strings that identify the tasks
        """
        assert isinstance(batch, Batch), "Requires a Batch object as input"
        assert len(batch.tasks) > 0, "Requires a non-empty batch"

        data = batch.prepare()

        # Send the data to funcX
        r = self.web_client.submit(data)

        task_uuids: t.List[str] = []
        for result in r["results"]:
            task_id = result["task_uuid"]
            task_uuids.append(task_id)
            if result["http_status_code"] != 200:
                # this method of handling errors for a batch response is not
                # ideal, as it will raise any error in the multi-response,
                # but it will do until batch_run is deprecated in favor of Executer
                handle_response_errors(result)

        if self.asynchronous:
            task_group_id = r["task_group_id"]
            asyncio_tasks = []
            for task_id in task_uuids:
                funcx_task = FuncXTask(task_id)
                asyncio_task = self.loop.create_task(funcx_task.get_result())
                asyncio_tasks.append(asyncio_task)

                self.ws_polling_task.add_task(funcx_task)
            self.ws_polling_task.put_task_group_id(task_group_id)
            return asyncio_tasks

        return task_uuids

    def register_endpoint(
        self, name, endpoint_id, metadata=None, endpoint_version=None
    ):
        """Register an endpoint with the funcX service.

        Parameters
        ----------
        name : str
            Name of the endpoint
        endpoint_id : str
                The uuid of the endpoint
        metadata : dict
            endpoint metadata, see default_config example
        endpoint_version: str
            Version string to be passed to the webService as a compatibility check

        Returns
        -------
        A dict
            {'endpoint_id' : <>,
             'address' : <>,
             'client_ports': <>}
        """
        self.version_check()

        r = self.web_client.register_endpoint(
            endpoint_name=name,
            endpoint_id=endpoint_id,
            metadata=metadata,
            endpoint_version=endpoint_version,
        )
        return r.data

    def get_containers(self, name, description=None):
        """
        Register a DLHub endpoint with the funcX service and get the containers to
        launch.

        Parameters
        ----------
        name : str
            Name of the endpoint
        description : str
            Description of the endpoint

        Returns
        -------
        int
            The port to connect to and a list of containers
        """
        data = {"endpoint_name": name, "description": description}

        r = self.web_client.post("get_containers", data=data)
        return r.data["endpoint_uuid"], r.data["endpoint_containers"]

    def get_container(self, container_uuid, container_type):
        """Get the details of a container for staging it locally.

        Parameters
        ----------
        container_uuid : str
            UUID of the container in question
        container_type : str
            The type of containers that will be used (Singularity, Shifter, Docker)

        Returns
        -------
        dict
            The details of the containers to deploy
        """
        self.version_check()

        r = self.web_client.get(f"containers/{container_uuid}/{container_type}")
        return r.data["container"]

    def get_endpoint_status(self, endpoint_uuid):
        """Get the status reports for an endpoint.

        Parameters
        ----------
        endpoint_uuid : str
            UUID of the endpoint in question

        Returns
        -------
        dict
            The details of the endpoint's stats
        """
        r = self.web_client.get_endpoint_status(endpoint_uuid)
        return r.data

    def register_function(
        self,
        function,
        function_name=None,
        container_uuid=None,
        description=None,
        public=False,
        group=None,
        searchable=True,
    ):
        """Register a function code with the funcX service.

        Parameters
        ----------
        function : Python Function
            The function to be registered for remote execution
        function_name : str
            The entry point (function name) of the function. Default: None
        container_uuid : str
            Container UUID from registration with funcX
        description : str
            Description of the file
        public : bool
            Whether or not the function is publicly accessible. Default = False
        group : str
            A globus group uuid to share this function with
        searchable : bool
            If true, the function will be indexed into globus search with the
            appropriate permissions

        Returns
        -------
        function uuid : str
            UUID identifier for the registered function
        """
        data = FunctionRegistrationData(
            function=function,
            failover_source="",
            container_uuid=container_uuid,
            entry_point=function_name,
            description=description,
            public=public,
            group=group,
            searchable=searchable,
            serializer=self.fx_serializer,
        )
        logger.info(f"Registering function : {data}")
        r = self.web_client.register_function(data)
        return r.data["function_uuid"]

    def search_function(self, q, offset=0, limit=10, advanced=False):
        """Search for function via the funcX service

        Parameters
        ----------
        q : str
            free-form query string
        offset : int
            offset into total results
        limit : int
            max number of results to return
        advanced : bool
            allows elastic-search like syntax in query string

        Returns
        -------
        FunctionSearchResults
        """
        return self.searcher.search_function(
            q, offset=offset, limit=limit, advanced=advanced
        )

    def search_endpoint(self, q, scope="all", owner_id=None):
        """

        Parameters
        ----------
        q
        scope : str
            Can be one of {'all', 'my-endpoints', 'shared-with-me'}
        owner_id
            should be urn like f"urn:globus:auth:identity:{owner_uuid}"

        Returns
        -------

        """
        return self.searcher.search_endpoint(q, scope=scope, owner_id=owner_id)

    def register_container(self, location, container_type, name="", description=""):
        """Register a container with the funcX service.

        Parameters
        ----------
        location : str
            The location of the container (e.g., its docker url). Required
        container_type : str
            The type of containers that will be used (Singularity, Shifter, Docker).
            Required

        name : str
            A name for the container. Default = ''
        description : str
            A description to associate with the container. Default = ''

        Returns
        -------
        str
            The id of the container
        """
        payload = {
            "name": name,
            "location": location,
            "description": description,
            "type": container_type,
        }

        r = self.web_client.post("containers", data=payload)
        return r.data["container_id"]

    def add_to_whitelist(self, endpoint_id, function_ids):
        """Adds the function to the endpoint's whitelist

        Parameters
        ----------
        endpoint_id : str
            The uuid of the endpoint
        function_ids : list
            A list of function id's to be whitelisted

        Returns
        -------
        json
            The response of the request
        """
        return self.web_client.whitelist_add(endpoint_id, function_ids)

    def get_whitelist(self, endpoint_id):
        """List the endpoint's whitelist

        Parameters
        ----------
        endpoint_id : str
            The uuid of the endpoint

        Returns
        -------
        json
            The response of the request
        """
        return self.web_client.get_whitelist(endpoint_id)

    def delete_from_whitelist(self, endpoint_id, function_ids):
        """List the endpoint's whitelist

        Parameters
        ----------
        endpoint_id : str
            The uuid of the endpoint
        function_ids : list
            A list of function id's to be whitelisted

        Returns
        -------
        json
            The response of the request
        """
        if not isinstance(function_ids, list):
            function_ids = [function_ids]
        res = []
        for fid in function_ids:
            res.append(self.web_client.whitelist_remove(endpoint_id, fid))
        return res
