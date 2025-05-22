from uuid import uuid4, uuid1
from app.config.config import settings
import os
import secrets
import logging

from fastapi import HTTPException, UploadFile, status
import boto3
from botocore.exceptions import ClientError
from uuid import uuid4

logging.basicConfig(level=logging.INFO)


aws_bucket_name = settings.S3_BUCKET_NAME
s3 = boto3.resource(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_KEY,
)

s3_client = boto3.client("s3")

# UPLOAD IMAGE TO AWS


# async def add_image(image: UploadFile):
#     token_name = secrets.token_hex(12)
#     file_name = f"{token_name}{image.filename}"

#     bucket = s3.Bucket(aws_bucket_name)
#     bucket.upload_fileobj(image.file, file_name)

#     image_url = f"https://{aws_bucket_name}.s3.amazonaws.com/{file_name}"

#     return image_url

async def add_image(image: UploadFile) -> str:
    """
    Upload an image to S3 and return its URL.

    Args:
        image: UploadFile object to upload

    Returns:
        str: URL of the uploaded image, or None if image is None
    """
    if not image:
        return None
    token_name = secrets.token_hex(12)
    file_name = f"{token_name}-{image.filename}"
    bucket = s3.Bucket(aws_bucket_name)
    bucket.upload_fileobj(image.file, file_name)
    image_url = f"https://{aws_bucket_name}.s3.amazonaws.com/{file_name}"
    return image_url


async def upload_multiple_images(images: list[UploadFile]):
    urls = []
    # Validate file type
    if not images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image is required",
        )

    if len(images) > 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="At most 4 images allowed"
        )

    for image in images:
        if not image.content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {image.filename} is not an image",
            )
        try:
            file_key = f"{uuid1()}-{image.filename}"
            # file_name = f"{token_name}{image.filename}"

            bucket = s3.Bucket(aws_bucket_name)
            bucket.upload_fileobj(image.file, file_key)

            url = f"https://{aws_bucket_name}.s3.amazonaws.com/{file_key}"
            urls.append(url)

        except ClientError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload image: {str(e)}",
            )

    return urls


async def add_profile_image(image: UploadFile) -> str:
    """
    Upload a single image to S3
    Args:
        image: UploadFile object
        
    Returns:
        str: Image URL
    """
    # Validate file type
    if not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image"
        )

    try:
        # Generate unique file name
        file_key = f"{uuid4()}-{image.filename}"

        # Upload to S3
        bucket = s3.Bucket(aws_bucket_name)
        bucket.upload_fileobj(
            image.file,
            file_key,
            # ExtraArgs={
            #     "ContentType": image.content_type,
            #     "ACL": "public-read"
            # }
        )

        # Generate and return URL
        image_url = f"https://{aws_bucket_name}.s3.amazonaws.com/{file_key}"
        logging.info(f"Successfully uploaded image: {file_key}")

        return image_url

    except ClientError as e:
        logging.error(f"Failed to upload image: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload image: {str(e)}",
        )
    except Exception as e:
        logging.error(f"Unexpected error uploading image: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )


# DELETE IMAGE FROM AWS


async def delete_s3_object(file_url: str) -> bool:
    """
    Delete an object from S3 using its URL
    Args:
        file_url: Full S3 URL of the object
    Returns:
        bool: True if deletion successful
    """
    try:
        # Extract key from URL
        key = file_url.split(f"{aws_bucket_name}.s3.amazonaws.com/")[1]

        bucket = s3.Bucket(aws_bucket_name)
        bucket.Object(key).delete()

        logging.info(f"Successfully deleted S3 object: {key}")
        return True

    except Exception as e:
        logging.error(f"Failed to delete S3 object: {str(e)}")
        return False


async def update_image(new_image: UploadFile, old_image_url: str) -> str:
    """
    Update an image by deleting the old one and uploading the new one.

    Args:
        new_image: New image to upload
        old_image_url: URL of image to replace

    Returns:
        str: New image URL, or None if new_image is None
    """
    if old_image_url:
        await delete_s3_object(old_image_url)
    if not new_image:
        return None
    return await add_image(new_image)

# async def update_image(
#     new_image: UploadFile,
#     old_image_url: str,
# ) -> str:
#     """
#     Update an image by deleting the old one and uploading the new one
#     Args:
#         new_image: New image to upload
#         old_image_url: URL of image to replace
#         folder: S3 folder path
#     Returns:
#         str: New image URL
#     """
#     # Delete old image
#     if old_image_url:
#         await delete_s3_object(old_image_url)

#     # Upload new image
#     return await add_image(new_image)


async def update_multiple_images(
    new_images: list[UploadFile],
    old_image_urls: list[str],
    folder: str = "Items-Images",
) -> list[str]:
    """
    Update multiple images by deleting old ones and uploading new ones
    Args:
        new_images: List of new images to upload
        old_image_urls: List of URLs to replace
        folder: S3 folder path
    Returns:
        list[str]: List of new image URLs
    """
    # Delete old images
    for url in old_image_urls:
        if url:
            await delete_s3_object(url)

    # Upload new images
    return await upload_multiple_images(new_images, folder)
