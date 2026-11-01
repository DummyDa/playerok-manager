# Playerok listing integration adapted from the original prototype.
from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from ..config import Config


PLAYEROK_TELEGRAM_GAME_ID = "1ed5d49c-9e13-69d0-3089-fe18e371a177"
PLAYEROK_TG_GIFTS_CATEGORY_ID = "1efcc43d-d46b-6b10-9805-49c1107e28b3"
PLAYEROK_TG_GIFT_OBTAINING_TYPE_ID = "1effb57d-3039-6a90-28fc-bb30c076c6dc"
PLAYEROK_TG_GIFT_COMMENT_FIELD_ID = "1effb57f-7c25-6790-faf7-dc76d7ee2625"
PLAYEROK_TG_GIFT_COMMENT_VALUE = "Ожидайте"

GAME_PAGE_CATEGORY_HASH = (
    "7759f743651176ddad6afefb5f2e889ec9984cae08a015281879cd61e94bdb60"
)
ITEM_PRIORITY_STATUSES_HASH = (
    "b922220c6f979537e1b99de6af8f5c13727daeff66727f679f07f986ce1c025a"
)
ITEM_HASH = "1cdb4b335f6c119db77883451f41cef83fc449f79f021627f27b76ec49203487"
ITEMS_HASH = "3f20c731f8f769a094ee3fa32e09f8e12250357e9a4f0ebb4e6988e7a0bb9260"
VIEWER_LOCAL_BALANCE_HASH = (
    "836ba5f37fd9377acacdfe93b879566f87791f8119704ee6844382af69248d48"
)
VIEWER_HAS_ENABLED_NOTIFICATIONS_HASH = (
    "e37e31e7b6ba73b9399bec6c1b8204b6efe98d6ab23f8a1015eba4ab0940c6c6"
)

PLAYEROK_ACTIVE_PROFILE_ITEM_STATUSES = (
    "APPROVED",
    "PENDING_MODERATION",
    "PENDING_APPROVAL",
)
PLAYEROK_COMPLETED_PROFILE_ITEM_STATUSES = (
    "DECLINED",
    "BLOCKED",
    "EXPIRED",
    "SOLD",
    "DRAFT",
    "DISCONTINUED",
)

CREATE_ITEM_QUERY = """
mutation createItem($input: CreateItemInput!, $attachments: [Upload!], $showForbiddenImage: Boolean) {
  createItem(input: $input, attachments: $attachments) {
    id
    slug
    name
    price
    rawPrice
    status
    attachments(showForbiddenImage: $showForbiddenImage) {
      id
      url
      __typename
    }
    __typename
  }
}
""".strip()

PUBLISH_ITEM_QUERY = """
mutation publishItem($input: PublishItemInput!, $showForbiddenImage: Boolean) {
  publishItem(input: $input) {
    id
    slug
    name
    price
    rawPrice
    status
    attachments(showForbiddenImage: $showForbiddenImage) {
      id
      url
      __typename
    }
    __typename
  }
}
""".strip()

UPDATE_ITEM_QUERY = """
mutation updateItem($input: UpdateItemInput!, $addedAttachments: [Upload!], $showForbiddenImage: Boolean) {
  updateItem(input: $input, addedAttachments: $addedAttachments) {
    id
    slug
    name
    price
    rawPrice
    status
    attachments(showForbiddenImage: $showForbiddenImage) {
      id
      url
      __typename
    }
    __typename
  }
}
""".strip()


class HTTPSession(Protocol):
    proxies: dict[str, str]

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        ...

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        ...


@dataclass(frozen=True)
class PlayerokListingInput:
    title: str
    collection: str
    price: int
    description: str
    image_bytes: bytes
    keep_in_sale: bool = False
    image_filename: str = "listing.png"
    image_mime: str | None = None
    premium: bool = False
    discount_price: int | None = None


@dataclass(frozen=True)
class PlayerokListingResult:
    item_id: str
    slug: str
    status: str | None
    premium: bool = False
    premium_price: int | None = None
    raw_price: int | None = None
    price: int | None = None
    discount_price: int | None = None
    name: str | None = None
    may_be_published: bool | None = None

    @property
    def url(self) -> str:
        return f"https://playerok.com/products/{self.slug}"


class PlayerokListingError(RuntimeError):
    pass


class PlayerokListingClient:
    def __init__(
        self,
        config: Config,
        cookies: str,
        session: HTTPSession | None = None,
    ) -> None:
        self.config = config
        self.cookies = cookies
        self.session: HTTPSession = session or requests.Session()
        self.graphql_url = "https://playerok.com/graphql"
        if config.playerok_proxy:
            self.session.proxies.update(
                {"http": config.playerok_proxy, "https": config.playerok_proxy}
            )

    def create_and_publish(self, listing: PlayerokListingInput, before_publish=None, draft=None) -> PlayerokListingResult:
        if draft is None:
            collection_slug = self.resolve_collection(listing.collection)
            draft = self.create_item(listing, collection_slug)
        item_id = _required_str(draft, "id", "createItem")
        raw_price = _optional_int(draft.get("rawPrice")) or listing.price
        price = _optional_int(draft.get("price")) or raw_price
        if listing.discount_price is not None:
            if listing.discount_price <= 0:
                raise PlayerokListingError("Цена со скидкой должна быть больше нуля.")
            if listing.discount_price >= raw_price:
                raise PlayerokListingError(
                    "Цена со скидкой должна быть меньше исходной цены."
                )
            discounted = self.update_item_price(item_id, listing.discount_price)
            raw_price = _optional_int(discounted.get("rawPrice")) or raw_price
            price = _optional_int(discounted.get("price")) or listing.discount_price
        priority_status = self.get_priority_status(
            item_id,
            price,
            premium=listing.premium,
        )
        priority_status_id = _required_str(
            priority_status,
            "id",
            "itemPriorityStatuses",
        )
        premium_price = int(priority_status.get("price") or 0) if listing.premium else None
        if listing.premium:
            self.get_viewer_has_enabled_notifications()
            self.get_viewer_local_balance()
        if before_publish is not None:before_publish(item_id,priority_status)
        published = self.publish_item(
            item_id,
            priority_status_id,
            premium=listing.premium,
            keep_in_sale=listing.keep_in_sale,
        )
        return PlayerokListingResult(
            item_id=_required_str(published, "id", "publishItem"),
            slug=_required_str(published, "slug", "publishItem"),
            status=_optional_str(published.get("status")),
            premium=listing.premium,
            premium_price=premium_price,
            raw_price=_optional_int(published.get("rawPrice")) or raw_price,
            price=_optional_int(published.get("price")) or price,
            discount_price=listing.discount_price,
            name=_optional_str(published.get("name")),
            may_be_published=_optional_bool(published.get("mayBePublished")),
        )

    def resolve_collection(self, user_value: str) -> str:
        requested = _normalize_collection_key(user_value)
        if not requested:
            raise PlayerokListingError("Укажи модель/коллекцию подарка.")

        options = self.get_collection_options()
        for option in options:
            value = _optional_str(option.get("value"))
            label = _optional_str(option.get("label"))
            if not value:
                continue
            candidates = {
                _normalize_collection_key(value),
                _normalize_collection_key(label),
            }
            if requested in candidates:
                return value

        labels = [
            str(option.get("label") or option.get("value"))
            for option in options[:12]
            if option.get("label") or option.get("value")
        ]
        hint = ", ".join(labels)
        if hint:
            raise PlayerokListingError(
                f"Не нашел такую коллекцию Playerok. Примеры: {hint}."
            )
        raise PlayerokListingError("Не получил список коллекций Playerok.")

    def get_collection_options(self) -> list[dict[str, Any]]:
        data = self._graphql_get_persisted(
            operation_name="GamePageCategory",
            variables={
                "id": PLAYEROK_TG_GIFTS_CATEGORY_ID,
                "gameId": PLAYEROK_TELEGRAM_GAME_ID,
                "slug": "nft",
            },
            sha256_hash=GAME_PAGE_CATEGORY_HASH,
            gql_path="/sell",
        )
        category = data.get("gameCategory") or {}
        options = category.get("options") or []
        return [
            option
            for option in options
            if isinstance(option, dict)
            and option.get("group") == "Коллекция"
            and option.get("field") == "type"
        ]

    def get_item(self, slug: str) -> dict[str, Any]:
        if not slug:
            raise PlayerokListingError("Slug объявления пустой.")

        data = self._graphql_get_persisted(
            operation_name="item",
            variables={
                "slug": slug,
                "hasSupportAccess": False,
                "showForbiddenImage": True,
            },
            sha256_hash=ITEM_HASH,
            gql_path="/products/[slug]",
        )
        return _required_dict(data, "item")

    def list_profile_items(
        self,
        user_id: str,
        statuses: tuple[str, ...] | list[str],
        completed: bool = False,
        page_size: int = 16,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        if not user_id:
            raise PlayerokListingError("Не получил userId Playerok для списка лотов.")
        if not statuses:
            raise PlayerokListingError("Не указаны статусы Playerok для списка лотов.")

        items: list[dict[str, Any]] = []
        after: str | None = None
        gql_path = (
            "/profile/[username]/products/completed"
            if completed
            else "/profile/[username]/products"
        )
        for _ in range(max(1, max_pages)):
            pagination: dict[str, Any] = {"first": int(page_size)}
            if after:
                pagination["after"] = after

            data = self._graphql_get_persisted(
                operation_name="items",
                variables={
                    "pagination": pagination,
                    "filter": {
                        "userId": user_id,
                        "status": list(statuses),
                        "withOfficial": False,
                    },
                    "showForbiddenImage": True,
                },
                sha256_hash=ITEMS_HASH,
                gql_path=gql_path,
            )
            payload = _required_dict(data, "items")
            edges = payload.get("edges") or []
            if not isinstance(edges, list):
                raise PlayerokListingError("Playerok вернул неожиданный список лотов.")
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                node = edge.get("node")
                if isinstance(node, dict):
                    items.append(node)

            page_info = payload.get("pageInfo") or {}
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
                break
            next_cursor = _optional_str(page_info.get("endCursor"))
            if not next_cursor or next_cursor == after:
                break
            after = next_cursor

        return items

    def create_item(
        self,
        listing: PlayerokListingInput,
        collection_slug: str,
    ) -> dict[str, Any]:
        if not listing.image_bytes:
            raise PlayerokListingError("Фото объявления пустое.")

        operations = build_create_item_operations(
            title=listing.title,
            collection_slug=collection_slug,
            price=listing.price,
            description=listing.description,
        )
        files = {
            "1": (
                listing.image_filename or "listing.png",
                listing.image_bytes,
                listing.image_mime
                or _guess_mime(listing.image_filename, listing.image_bytes),
            )
        }
        payload = {
            "operations": json.dumps(operations, ensure_ascii=False),
            "map": json.dumps({"1": ["variables.attachments.0"]}),
        }
        data = self._graphql_post_multipart(
            payload,
            files,
            operation_name="createItem",
            gql_path="/sell",
        )
        return _required_dict(data, "createItem")

    def update_item_price(self, item_id: str, price: int) -> dict[str, Any]:
        if price <= 0:
            raise PlayerokListingError("Цена должна быть больше нуля.")

        return self.update_item(item_id, price=price)

    def update_item(
        self,
        item_id: str,
        *,
        price: int | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        if not item_id:
            raise PlayerokListingError("ID лота пустой.")
        if price is not None and price <= 0:
            raise PlayerokListingError("Цена должна быть больше нуля.")
        if name is not None and not name.strip():
            raise PlayerokListingError("Название лота не может быть пустым.")

        input_payload: dict[str, Any] = {"id": item_id}
        if price is not None:
            input_payload["price"] = int(price)
        if name is not None:
            input_payload["name"] = name.strip()
        if description is not None:
            input_payload["description"] = description.strip()
        if len(input_payload) == 1:
            raise PlayerokListingError("Не указаны изменения для лота.")

        payload = {
            "operationName": "updateItem",
            "variables": {
                "input": input_payload,
                "addedAttachments": None,
                "showForbiddenImage": True,
            },
            "query": UPDATE_ITEM_QUERY,
        }
        data = self._graphql_post_json(
            payload,
            operation_name="updateItem",
            gql_path="/products/[slug]/edit",
        )
        return _required_dict(data, "updateItem")

    def get_default_priority_status_id(self, item_id: str, price: int) -> str:
        return _required_str(
            self.get_priority_status(item_id, price, premium=False),
            "id",
            "itemPriorityStatuses",
        )

    def relist_item(
        self,
        item_id: str,
        price: int,
        premium: bool = False,
    ) -> PlayerokListingResult:
        priority_status = self.get_priority_status(
            item_id,
            price,
            premium=premium,
        )
        priority_status_id = _required_str(
            priority_status,
            "id",
            "itemPriorityStatuses",
        )
        premium_price = int(priority_status.get("price") or 0) if premium else None
        if premium:
            self.get_viewer_has_enabled_notifications()
            self.get_viewer_local_balance()

        published = self.publish_item(
            item_id,
            priority_status_id,
            premium=premium,
        )
        raw_price = _optional_int(published.get("rawPrice"))
        result_price = _optional_int(published.get("price")) or price
        discount_price = (
            result_price
            if raw_price is not None and result_price < raw_price
            else None
        )
        return PlayerokListingResult(
            item_id=_required_str(published, "id", "publishItem"),
            slug=_required_str(published, "slug", "publishItem"),
            status=_optional_str(published.get("status")),
            premium=premium,
            premium_price=premium_price,
            raw_price=raw_price,
            price=result_price,
            discount_price=discount_price,
            name=_optional_str(published.get("name")),
            may_be_published=_optional_bool(published.get("mayBePublished")),
        )

    def get_priority_status(
        self,
        item_id: str,
        price: int,
        premium: bool,
    ) -> dict[str, Any]:
        data = self._graphql_get_persisted(
            operation_name="itemPriorityStatuses",
            variables={"itemId": item_id, "price": price},
            sha256_hash=ITEM_PRIORITY_STATUSES_HASH,
            gql_path="/products/[slug]",
        )
        statuses = data.get("itemPriorityStatuses") or []
        if not isinstance(statuses, list):
            raise PlayerokListingError("Playerok вернул неожиданный список приоритетов.")

        if premium:
            for status in statuses:
                if (
                    isinstance(status, dict)
                    and status.get("type") == "PREMIUM"
                    and status.get("id")
                ):
                    return status
            raise PlayerokListingError("Не нашел премиум-продвижение для этого объявления.")

        for status in statuses:
            if (
                isinstance(status, dict)
                and status.get("type") == "DEFAULT"
                and int(status.get("price") or 0) == 0
                and status.get("id")
            ):
                return status
        for status in statuses:
            if (
                isinstance(status, dict)
                and int(status.get("price") or 0) == 0
                and status.get("id")
            ):
                return status
        raise PlayerokListingError("Не нашел бесплатный обычный приоритет объявления.")

    def get_viewer_local_balance(self) -> dict[str, Any]:
        data = self._graphql_get_persisted(
            operation_name="viewerLocalBalance",
            variables={},
            sha256_hash=VIEWER_LOCAL_BALANCE_HASH,
            gql_path="/products/[slug]",
        )
        viewer = data.get("viewer")
        if not isinstance(viewer, dict):
            raise PlayerokListingError("Playerok response has no viewer balance.")
        return viewer

    def get_viewer_has_enabled_notifications(self) -> dict[str, Any]:
        data = self._graphql_get_persisted(
            operation_name="viewerHasEnabledNotifications",
            variables={},
            sha256_hash=VIEWER_HAS_ENABLED_NOTIFICATIONS_HASH,
            gql_path="/products/[slug]",
        )
        viewer = data.get("viewer")
        if not isinstance(viewer, dict):
            raise PlayerokListingError("Playerok response has no viewer notifications.")
        return viewer

    def publish_item(
        self,
        item_id: str,
        priority_status_id: str,
        premium: bool = False,
        keep_in_sale: bool = False,
    ) -> dict[str, Any]:
        input_payload: dict[str, Any] = {
            "transactionProviderId": "LOCAL",
            "priorityStatuses": [priority_status_id],
            "itemId": item_id,
            "keepInSale": keep_in_sale,
        }
        if premium:
            input_payload["transactionProviderData"] = {"paymentMethodId": None}

        payload = {
            "operationName": "publishItem",
            "variables": {
                "input": input_payload,
                "showForbiddenImage": True,
            },
            "query": PUBLISH_ITEM_QUERY,
        }
        data = self._graphql_post_json(
            payload,
            operation_name="publishItem",
            gql_path="/products/[slug]",
        )
        return _required_dict(data, "publishItem")

    def _graphql_get_persisted(
        self,
        operation_name: str,
        variables: dict[str, Any],
        sha256_hash: str,
        gql_path: str,
    ) -> dict[str, Any]:
        params = {
            "operationName": operation_name,
            "variables": json.dumps(variables, ensure_ascii=False, separators=(",", ":")),
            "extensions": json.dumps(
                {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}},
                separators=(",", ":"),
            ),
        }
        try:
            response = self.session.get(
                self.graphql_url,
                params=params,
                headers=self._headers(operation_name, gql_path, accept="*/*"),
                timeout=self.config.playerok_request_timeout,
            )
        except Exception as exc:
            if _is_transport_error(exc):
                raise PlayerokListingError(
                    _format_transport_error(operation_name, exc)
                ) from exc
            raise
        return self._decode_graphql_response(response)

    def _graphql_post_json(
        self,
        payload: dict[str, Any],
        operation_name: str,
        gql_path: str,
    ) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.graphql_url,
                json=payload,
                headers=self._headers(
                    operation_name,
                    gql_path,
                    accept="*/*",
                    content_type="application/json",
                ),
                timeout=self.config.playerok_request_timeout,
            )
        except Exception as exc:
            if _is_transport_error(exc):
                raise PlayerokListingError(
                    _format_transport_error(operation_name, exc)
                ) from exc
            raise
        return self._decode_graphql_response(response)

    def _graphql_post_multipart(
        self,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        operation_name: str,
        gql_path: str,
    ) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.graphql_url,
                data=data,
                files=files,
                headers=self._headers(operation_name, gql_path, accept="*/*"),
                # Uploading/rendering listing images can outlive normal reads.
                # Keep a bounded connection timeout and never retry a POST.
                timeout=(min(30,self.config.playerok_request_timeout),max(120,self.config.playerok_request_timeout)),
            )
        except Exception as exc:
            if _is_transport_error(exc):
                raise PlayerokListingError(
                    _format_transport_error(operation_name, exc)
                ) from exc
            raise
        return self._decode_graphql_response(response)

    def _headers(
        self,
        operation_name: str,
        gql_path: str,
        accept: str,
        content_type: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "accept": accept,
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "apollo-require-preflight": "true",
            "apollographql-client-name": "web",
            "cookie": self.cookies,
            "origin": "https://playerok.com",
            "referer": "https://playerok.com/sell",
            "user-agent": self.config.playerok_user_agent,
            "x-gql-op": operation_name,
            "x-gql-path": gql_path,
            "x-timezone-offset": "-180",
        }
        if content_type:
            headers["content-type"] = content_type
        return headers

    def _decode_graphql_response(self, response: requests.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            text = getattr(response, "text", "")
            raise PlayerokListingError(f"Playerok HTTP error: {text[:700]}") from exc
        except ValueError as exc:
            text = getattr(response, "text", "")
            raise PlayerokListingError(
                f"Playerok returned invalid JSON: {text[:700]}"
            ) from exc
        except Exception as exc:
            if _is_transport_error(exc):
                raise PlayerokListingError(
                    _format_transport_error("GraphQL", exc)
                ) from exc
            raise

        errors = payload.get("errors")
        if errors:
            raise PlayerokListingError(_format_graphql_errors(errors))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlayerokListingError("Playerok response has no data object.")
        return data


def build_create_item_operations(
    title: str,
    collection_slug: str,
    price: int,
    description: str,
) -> dict[str, Any]:
    return {
        "operationName": "createItem",
        "variables": {
            "input": {
                "gameCategoryId": PLAYEROK_TG_GIFTS_CATEGORY_ID,
                "obtainingTypeId": PLAYEROK_TG_GIFT_OBTAINING_TYPE_ID,
                "attributes": {"type": collection_slug},
                "name": title,
                "price": int(price),
                "description": description,
                "dataFields": [
                    {
                        "fieldId": PLAYEROK_TG_GIFT_COMMENT_FIELD_ID,
                        "value": PLAYEROK_TG_GIFT_COMMENT_VALUE,
                    }
                ],
            },
            "attachments": [None],
            "showForbiddenImage": True,
        },
        "query": CREATE_ITEM_QUERY,
    }


def _guess_mime(filename: str, content: bytes) -> str:
    guessed = mimetypes.guess_type(filename or "")[0]
    if guessed:
        return guessed
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _normalize_collection_key(value: str | None) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-zа-я0-9-]+", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _required_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise PlayerokListingError(f"Playerok response has no {key} object.")
    return value


def _required_str(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if value is None or str(value) == "":
        raise PlayerokListingError(f"Playerok {context} response has no {key}.")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value if value else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    value = str(value).strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _format_graphql_errors(errors: Any) -> str:
    if not isinstance(errors, list):
        return f"Playerok GraphQL error: {errors}"
    messages = []
    for error in errors[:3]:
        if isinstance(error, dict):
            messages.append(str(error.get("message") or error))
        else:
            messages.append(str(error))
    return "Playerok GraphQL error: " + "; ".join(messages)


def _is_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.RequestException, OSError, TimeoutError)):
        return True
    module = exc.__class__.__module__
    if module.startswith(("curl_cffi", "httpx", "httpcore", "urllib3")):
        return True
    class_names = {cls.__name__ for cls in exc.__class__.__mro__}
    return bool(
        class_names
        & {
            "ConnectError",
            "ConnectionError",
            "NetworkError",
            "ProxyError",
            "RequestException",
            "RequestsError",
            "Timeout",
            "TimeoutException",
        }
    )


def _format_transport_error(operation_name: str, exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"Playerok недоступен при запросе {operation_name}: {detail[:700]}"
