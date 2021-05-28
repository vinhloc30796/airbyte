from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import pendulum
import requests
import backoff

# Chargebee
import chargebee
from chargebee.main import ChargeBee
from chargebee.list_result import ListResult  # stores next_offset
from chargebee.models import Subscription, Customer, Invoice, Order, Plan
from chargebee.api_error import OperationFailedError

# Airbyte
from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.sources.streams import Stream
from airbyte_cdk.models import AirbyteStream, SyncMode
from airbyte_cdk.logger import AirbyteLogger

# Backoff params below
# according to Chargebee's guidance on rate limit
# https://apidocs.chargebee.com/docs/api?prod_cat_ver=2#api_rate_limits
MAX_TRIES = 10  # arbitrary max_tries
MAX_TIME = 90  # because Chargebee API enforce a per-minute limit


class ChargebeeStream(Stream):
    supports_incremental = True
    primary_key = "id"
    default_cursor_field = "updated_at"
    logger = AirbyteLogger()

    def __init__(self):
        self.next_offset = None
        # Request params below
        # according to Chargebee's guidance on pagination
        # https://apidocs.chargebee.com/docs/api/#pagination_and_filtering
        self.params = {
            "limit": 100,  # Limit at 100
            "sort_by[asc]": self.default_cursor_field,  # Sort ascending by updated_at
        }
        super().__init__()


    @backoff.on_exception(
        backoff.expo,  # Exponential back-off
        OperationFailedError,  # Only on Chargebee's OperationFailedError
        max_tries=MAX_TRIES,
        max_time=MAX_TIME,
    )
    def _send_request(self) -> ListResult:
        """
        Just a wrapper to allow @backoff decorator
        Reference: https://apidocs.chargebee.com/docs/api/#error_codes_list
        """
        # From Chargebee
        # Link: https://apidocs.chargebee.com/docs/api/#api_rate_limits
        list_result = self.api.list(self.params)
        return list_result

    def read_records(
        self,
        sync_mode: SyncMode,
        cursor_field: List[str] = None,
        stream_slice: Mapping[str, Any] = None,
        stream_state: Mapping[str, Any] = None,
    ) -> Iterable[Mapping[str, Any]]:
        """
        Override airbyte_cdk Stream's read_records method
        """
        # Add offset to params if found
        # Reference for Chargebee's pagination strategy below:
        # https://apidocs.chargebee.com/docs/api/#pagination_and_filtering
        pagination_completed = False
        if stream_state:
            self.params.update(stream_state)
        # Loop until pagination is completed
        while not pagination_completed:
            # Request the ListResult object from Chargebee
            # with back-off implemented through self._send_request()
            list_result = self._send_request()
            # Read message from results
            for message in list_result:
                yield message._response[self.name]
            # Get next page token
            self.next_offset = list_result.next_offset
            if self.next_offset:
                self.params.update({"offset": self.next_offset})
            else:
                pagination_completed = True

        # Always return an empty generator just in case no records were ever yielded
        yield from []

    def get_updated_state(
        self,
        current_stream_state: MutableMapping[str, Any],
        latest_record: Mapping[str, Any],
    ):
        """
        Override airbyte_cdk Stream's get_updated_state method
        to get the latest Chargebee stream state
        """
        # Init the current_stream_state
        current_stream_state = current_stream_state or {}

        # Get current timestamp
        # so Stream will sync all records
        # that have been updated before now
        now = pendulum.now().int_timestamp
        current_stream_state.update(
            {
                "update_at[before]": now,
            }
        )

        # Get the updated_at field from the latest record
        # using Chargebee's Model class
        # so Stream will sync all records
        # that have been updated since then
        print(latest_record)
        latest_updated_at = latest_record.get("updated_at")
        if latest_updated_at:
            current_stream_state.update(
                {
                    "update_at[after]": latest_updated_at,
                }
            )

        return current_stream_state


class SubscriptionStream(ChargebeeStream):
    name = "subscription"
    api = Subscription


class CustomerStream(ChargebeeStream):
    name = "customer"
    api = Customer


class InvoiceStream(ChargebeeStream):
    name = "invoice"
    api = Invoice

class OrderStream(ChargebeeStream):
    name = "order"
    api = Order

class PlanStream(ChargebeeStream):
    name = "plan"
    api = Plan

class SourceChargebee(AbstractSource):
    # Class variables
    LIMIT = 100

    def check_connection(self, logger, config: Mapping[str, Any]) -> Tuple[bool, any]:
        # Configure the Chargebee Python SDK
        chargebee.configure(
            api_key=config["site_api_token"],
            site=config["site"],
        )
        try:
            # Get one subscription to test connection
            Subscription.list(
                # Set limit
                # to test on a small dataset
                params={
                    "limit": 1,
                },
            )
            return True, None
        except Exception as err:
            # Should catch all exceptions
            # which are already handled by
            # Chargebee Python wrapper
            # https://github.com/chargebee/chargebee-python/blob/5346d833781de78a9eedbf9d12502f52c617c2d2/chargebee/http_request.py
            return False, str(err)

    def streams(self, config) -> List[Stream]:
        # Configure the Chargebee Python SDK
        chargebee.configure(
            api_key=config["site_api_token"],
            site=config["site"],
        )
        # Add the streams
        streams = [
            SubscriptionStream(),
            CustomerStream(),
            InvoiceStream(),
            OrderStream(),
            PlanStream(),
        ]
        return streams
