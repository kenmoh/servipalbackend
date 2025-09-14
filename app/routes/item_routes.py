from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Request, UploadFile, status, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.item_schemas import (
    CategoryCreate,
    CategoryResponse,
    MenuItemCreate,
    MenuResponseSchema,
    ItemType,
    FoodGroup,
)
from app.services import item_service
from app.utils.limiter import limiter
# from app.utils.s3_service import convert_video_to_gif, get_conversion_status

router = APIRouter(prefix="/api/items", tags=["Items"])


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item category",
    description="Allows VENDOR users to create a new category for items.",
)
async def create_new_category(
    category_data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryResponse:
    """
    Endpoint to create a new item category.
    - Requires authenticated VENDOR user.
    """
    return await item_service.create_category(db, current_user, category_data)


@router.post(
    "/menu-item-create",
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def create_menu_item(
    request: Request,
    name: str = Form(...),
    description: str = Form(None),
    price: Decimal = Form(...),
    side: str = Form(None),
    category_id: UUID | None = Form(None),
    food_group: FoodGroup | None = Form(None),
    item_type: ItemType = Form(...),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MenuResponseSchema:
    """
    Endpoint to create a new food item.
    - Requires authenticated VENDOR user.
    - The specified category_id must exist.
    """

    item_data = MenuItemCreate(
        name=name,
        description=description or None,
        price=price,
        images=images,
        item_type=item_type,
        category_id=category_id,
        food_group=food_group,
        side=side or None,
    )

    return await item_service.create_menu_item(
        db=db, current_user=current_user, item_data=item_data, images=images
    )


# @router.post(
#     "/laundry-item-create",
#     status_code=status.HTTP_201_CREATED,
# )
# async def create_laundry_item(
#     name: str = Form(...),
#     description: str = Form(None),
#     price: Decimal = Form(...),
#     images: list[UploadFile] = File(...),
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> LaundryMenuResponseSchema:
#     """
#     Endpoint to create a new laundry item.
#     - Requires authenticated VENDOR user.
#     - The specified category_id must exist.
#     """

#     item_data = LaundryItemCreate(
#         name=name,
#         description=description,
#         price=price,
#         images=images,
#     )

#     return await item_service.create_laundry_item(
#         db=db, current_user=current_user, item_data=item_data, images=images
#     )


@router.get("/categories", status_code=status.HTTP_200_OK)
async def get_categories(
    db: AsyncSession = Depends(get_db),
) -> list[CategoryResponse]:
    """
    Endpoint to get all categories.
    """
    return await item_service.get_categories(db)


@router.get("/foods", status_code=status.HTTP_200_OK)
async def get_all_food_items(
    db: AsyncSession = Depends(get_db),
) -> list[MenuResponseSchema]:
    """
    Endpoint to get all food items.
    """
    return await item_service.get_all_food_items(db)


@router.get("/laundries", status_code=status.HTTP_200_OK)
async def get_all_laundry_items(
    db: AsyncSession = Depends(get_db),
) -> list[MenuResponseSchema]:
    """
    Endpoint to get all laundry items.
    """
    return await item_service.get_all_laundry_items(db)


# @router.get(
#     "/{vendor_id}/restaurant",
#     status_code=status.HTTP_200_OK,
# )
# async def get_restaurant_menu(
#     vendor_id: UUID,
#     db: AsyncSession = Depends(get_db),
# ) -> list[RestaurantMenuResponseSchema]:
#     """
#     Endpoint to get all items for the logged-in VENDOR user.
#     """

#     return await item_service.get_restaurant_menu(db=db, vendor_id=vendor_id)


# @router.get(
#     "/{vendor_id}/lundry",
#     status_code=status.HTTP_200_OK,
# )
# async def get_lundry_menu(
#     vendor_id: UUID,
#     db: AsyncSession = Depends(get_db),
# ) -> list[LaundryMenuResponseSchema]:
#     """
#     Endpoint to get all items for the logged-in VENDOR user.
#     """

#     return await item_service.get_laundry_menu(db=db, user_id=vendor_id)


@router.get(
    "/{item_id}/item",
    status_code=status.HTTP_200_OK,
)
async def get_menu_item_by_id(
    item_id: UUID, db: AsyncSession = Depends(get_db)
) -> MenuResponseSchema:
    """
    Endpoint to retrieve a specific item by its UUID.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    """
    return await item_service.get_menu_item_by_id(db, item_id)


@router.put(
    "/{item_id}/update",
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("5/minute")
async def update_menu_item(
    request: Request,
    item_id: UUID,
    # item_data: MenuItemCreate,
    name: str = Form(...),
    item_type: str = Form(...),
    description: str = Form(...),
    price: Decimal = Form(...),
    category_id: UUID = Form(...),
    db: AsyncSession = Depends(get_db),
    images: list[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
) -> MenuResponseSchema:
    """
    Endpoint to update an existing item.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    - Returns 404 if the target category_id does not exist.
    """
    item_data = MenuItemCreate(name=name, item_type=item_type, description=description, price=price, category_id=category_id)
    return await item_service.update_menu_item(
        db=db,
        current_user=current_user,
        menu_item_id=item_id,
        item_data=item_data,
        images=images,
    )


@router.delete(
    "/{item_id}/delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Endpoint to delete an existing item.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    - Returns 204 No Content on successful deletion.
    """
    await item_service.delete_item(db, current_user, item_id)
    return None


# @router.post(
#     "/convert-video-to-gif",
#     status_code=status.HTTP_200_OK,
#     summary="Convert video to GIF",
#     description="Upload a video file (max 2 minutes, max 25MB) and convert it to GIF format. The video is temporarily stored in Appwrite, converted to GIF, and then the video is deleted.",
# )
# @limiter.limit("3/minute")
# async def convert_video_to_gif_endpoint(
#     request: Request,
#     video: UploadFile = File(..., description="Video file to convert (max 2 minutes, max 25MB)"),
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> dict:
#     """
#     Endpoint to convert a video file to GIF format.
#     - Requires authenticated user.
#     - Video must be under 2 minutes and 25MB.
#     - Returns GIF URL after successful conversion.
#     - Video file is automatically deleted after conversion.
#     """
#     try:
#         result = await convert_video_to_gif(video)
#         return result
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=str(e)
#         )


# @router.get(
#     "/conversion-status/{task_id}",
#     status_code=status.HTTP_200_OK,
#     summary="Check video conversion status",
#     description="Check the status of a video to GIF conversion task and get the GIF URL if completed.",
# )
# async def check_conversion_status(
#     task_id: str,
#     gif_filename: str,
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> dict:
#     """
#     Endpoint to check the status of a video to GIF conversion.
#     - Requires authenticated user.
#     - Returns status and GIF URL if conversion is complete.
#     """
#     try:
#         result = await get_conversion_status(task_id, gif_filename)
#         return result
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=str(e)
#         )


@router.post(
    "/process-completed-videos",
    status_code=status.HTTP_200_OK,
    summary="Process completed video conversions",
    description="Process all completed video to GIF conversions and update ItemImage records with final GIF URLs.",
)
async def process_completed_videos_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Endpoint to process completed video conversions and update ItemImage records.
    - Requires authenticated user.
    - Finds all pending video conversions and updates them with final GIF URLs.
    - Returns count of updated records.
    """
    try:
        from app.utils.s3_service import process_completed_video_conversions
        
        updated_count = await process_completed_video_conversions(db)
        
        return {
            "status": "success",
            "message": f"Processed {updated_count} completed video conversions",
            "updated_count": updated_count
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
