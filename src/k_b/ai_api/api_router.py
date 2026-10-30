"""The routers of the AI API, gathered in the one create_app plugs in."""

from fastapi import APIRouter

from k_b.ai_api.routers import digest_router

api_router = APIRouter()
api_router.include_router(digest_router.router)
