import json as _json
from pydantic import BaseModel, ValidationError
from typing import Optional, List, Tuple, Any

import httpx

from ktoolbox._enum import RetCodeEnum
from ktoolbox.api import BaseAPI, APIRet
from ktoolbox.api.model import Post, Revision
from ktoolbox.utils import generate_msg

__all__ = ["GetPost", "get_post"]


class PostProps(BaseModel):
    """Properties object containing additional post metadata"""
    flagged: Optional[Any] = None
    revisions: Optional[List[Tuple[int, Revision]]] = None


class GetPost(BaseAPI):
    path = "/{service}/user/{creator_id}/post/{post_id}"
    method = "get"

    class Response(BaseModel):
        post: Post
        props: Optional[PostProps] = None

    @classmethod
    def handle_res(cls, res: httpx.Response) -> APIRet:
        """
        Auto-detect response format:
        - kemono.cr:  { "post": {...}, "props": {...} }
        - pawchive.st: { "id": ..., "file": ..., ... }  (Post object directly)
        """
        try:
            raw = _json.loads(res.text)
        except ValueError as e:
            return APIRet(
                code=RetCodeEnum.JsonDecodeError,
                message=generate_msg(url=res.url, status_code=res.status_code, response=res.text),
                exception=e
            )
        if isinstance(raw, dict) and "post" in raw:
            # kemono.cr format: delegate to default handler
            return super().handle_res(res)
        else:
            # pawchive.st format: Post object returned directly
            try:
                post = Post.model_validate(raw)
                return APIRet(data=cls.Response(post=post))
            except ValidationError as e:
                return APIRet(
                    code=RetCodeEnum.ValidationError,
                    message=generate_msg(url=res.url, status_code=res.status_code, response=res.text),
                    exception=e
                )

    @classmethod
    async def __call__(cls, service: str, creator_id: str, post_id: str, revision_id: Optional[str] = None) -> APIRet[Response]:
        """
        Get a specific post or revision

        :param service: The service name
        :param creator_id: The creator's ID
        :param post_id: The post ID
        :param revision_id: The revision ID (optional, for revision posts)
        """
        if revision_id:
            path = f"/{service}/user/{creator_id}/post/{post_id}/revision/{revision_id}"
        else:
            path = cls.path.format(
                service=service,
                creator_id=creator_id,
                post_id=post_id
            )
        
        return await cls.request(path=path)


get_post = GetPost.__call__
