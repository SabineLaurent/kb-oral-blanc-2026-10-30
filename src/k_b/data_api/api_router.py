"""The routers of the data API, gathered in the one create_app plugs in."""

from fastapi import APIRouter

from k_b.data_api.routers import item_router, trend_router

api_router = APIRouter()
api_router.include_router(item_router.router)
api_router.include_router(trend_router.router)
