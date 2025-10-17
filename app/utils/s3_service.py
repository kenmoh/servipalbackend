
from app.config.config import settings
import secrets
import logging
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
import boto3
from botocore.exceptions import ClientError
from moviepy import VideoFileClip
from appwrite.client import Client
from appwrite.services.storage import Storage
from appwrite.input_file import InputFile
import dramatiq

from uuid import uuid4

logging.basicConfig(level=logging.INFO)

# Initialize Dramatiq broker
# dramatiq.set_broker(dramatiq.brokers.Redis(url=settings.REDIS_HOST))

# Initialize Appwrite client
# appwrite_client = Client()
# appwrite_client.set_endpoint(settings.APPWRITE_ENDPOINT)
# appwrite_client.set_project(settings.APPWRITE_PROJECT_ID)
# appwrite_client.set_key(settings.APPWRITE_API_KEY)

# # Initialize Appwrite storage
# appwrite_storage = Storage(appwrite_client)

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
            file_key = f"{uuid4()}-{image.filename}"
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


# @dramatiq.actor
# def process_video_to_gif_background(video_filename: str, gif_filename: str, video_url: str):
#     """
#     Background task to convert video to GIF using Dramatiq
#     """
#     try:
#         logging.info(f"Starting background video to GIF conversion: {video_filename}")
        
#         # Initialize Appwrite client inside the function to avoid circular imports
#         from app.config.config import settings
#         from appwrite.client import Client
#         from appwrite.services.storage import Storage
#         from appwrite.input_file import InputFile
        
#         appwrite_client = Client()
#         appwrite_client.set_endpoint(settings.APPWRITE_ENDPOINT)
#         appwrite_client.set_project(settings.APPWRITE_PROJECT_ID)
#         appwrite_client.set_key(settings.APPWRITE_API_KEY)
#         appwrite_storage = Storage(appwrite_client)
        
#         # Convert video to GIF using moviepy with the video URL
#         with VideoFileClip(video_url) as video_clip:
#             # Check duration (max 2 minutes = 120 seconds)
#             if video_clip.duration > 120:
#                 # Trim video to first 2 minutes
#                 video_clip = video_clip.subclip(0, 120)
#                 logging.info("Video trimmed to 2 minutes")
            
#             # Convert to GIF with optimized settings
#             gif_path = os.path.join(tempfile.gettempdir(), gif_filename)
#             video_clip.write_gif(
#                 gif_path,
#                 fps=15,  # Reduced FPS for smaller file size
#                 verbose=False,
#                 logger=None
#             )
        
#         # Upload GIF to Appwrite storage
#         logging.info(f"Uploading GIF to Appwrite: {gif_filename}")
#         with open(gif_path, 'rb') as gif_file:
#             appwrite_storage.create_file(
#                 bucket_id=settings.APPWRITE_BUCKET_ID,
#                 file_id=gif_filename,
#                 file=InputFile.from_path(gif_path, filename=gif_filename)
#             )
        
#         # Delete video from Appwrite storage
#         logging.info(f"Deleting video from Appwrite: {video_filename}")
#         appwrite_storage.delete_file(
#             bucket_id=settings.APPWRITE_BUCKET_ID,
#             file_id=video_filename
#         )
        
#         # Clean up temporary GIF file
#         os.unlink(gif_path)
        
#         logging.info("Background video to GIF conversion completed successfully")
        
#     except Exception as e:
#         logging.error(f"Error in background video to GIF conversion: {str(e)}")
#         # Try to clean up video if conversion failed
#         try:
#             if 'appwrite_storage' in locals():
#                 appwrite_storage.delete_file(
#                     bucket_id=settings.APPWRITE_BUCKET_ID,
#                     file_id=video_filename
#                 )
#         except Exception as cleanup_error:
#             logging.error(f"Failed to cleanup video file: {cleanup_error}")
#         raise


# async def convert_video_to_gif(video_file: UploadFile) -> dict:
#     """
#     Convert video to GIF with the following workflow:
#     1. Upload video to Appwrite storage (max 2 minutes, max 25MB)
#     2. Start background task to convert video to GIF
#     3. Return task ID for tracking
    
#     Args:
#         video_file: UploadFile object containing the video
        
#     Returns:
#         dict: Contains 'task_id', 'status', and 'message' keys
        
#     Raises:
#         HTTPException: If video validation fails or processing errors occur
#     """
#     try:
#         # Validate video file
#         if not video_file.content_type.startswith("video/"):
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="File must be a video"
#             )
        
#         # Check file size (25MB = 25 * 1024 * 1024 bytes)
#         max_size = 25 * 1024 * 1024
#         video_file.file.seek(0, 2)  # Seek to end
#         file_size = video_file.file.tell()
#         video_file.file.seek(0)  # Reset to beginning
        
#         if file_size > max_size:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Video file size must be less than 25MB"
#             )
        
#         # Generate unique filename
#         video_filename = f"{uuid4()}-{video_file.filename}"
#         gif_filename = f"{uuid4()}-{Path(video_file.filename).stem}.gif"
        
#         # Upload video to Appwrite storage
#         logging.info(f"Uploading video to Appwrite: {video_filename}")
        
#         # Create a temporary file from the UploadFile
#         with tempfile.NamedTemporaryFile(delete=False, suffix=Path(video_file.filename).suffix) as temp_file:
#             video_file.file.seek(0)
#             temp_file.write(video_file.file.read())
#             temp_file_path = temp_file.name
        
#         try:
#             appwrite_storage.create_file(
#                 bucket_id=settings.APPWRITE_BUCKET_ID,
#                 file_id=video_filename,
#                 file=InputFile.from_path(temp_file_path, filename=video_filename)
#             )
            
#             video_url = f"{settings.APPWRITE_ENDPOINT}/storage/buckets/{settings.APPWRITE_BUCKET_ID}/files/{video_filename}/view?project={settings.APPWRITE_PROJECT_ID}"
            
#             # Start background task
#             task = process_video_to_gif_background.send(video_filename, gif_filename, video_url)
            
#             logging.info(f"Started background video to GIF conversion task: {task.id}")
            
#             return {
#                 "task_id": task.id,
#                 "status": "processing",
#                 "message": "Video uploaded and conversion started in background",
#                 "video_filename": video_filename,
#                 "gif_filename": gif_filename
#             }
            
#         finally:
#             # Clean up temporary file
#             if os.path.exists(temp_file_path):
#                 os.unlink(temp_file_path)
        
#     except Exception as e:
#         logging.error(f"Unexpected error in convert_video_to_gif: {str(e)}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="An unexpected error occurred during video processing"
#         )


# async def get_conversion_status(task_id: str, gif_filename: str) -> dict:
#     """
#     Check the status of a video to GIF conversion task
    
#     Args:
#         task_id: Dramatiq task ID
#         gif_filename: Expected GIF filename
        
#     Returns:
#         dict: Contains status and GIF URL if available
#     """
#     try:
#         # Check if GIF exists in Appwrite storage
#         try:
#             gif_info = appwrite_storage.get_file(
#                 bucket_id=settings.APPWRITE_BUCKET_ID,
#                 file_id=gif_filename
#             )
            
#             # If GIF exists, conversion is complete
#             gif_url = f"{settings.APPWRITE_ENDPOINT}/storage/buckets/{settings.APPWRITE_BUCKET_ID}/files/{gif_filename}/view?project={settings.APPWRITE_PROJECT_ID}"
            
#             return {
#                 "task_id": task_id,
#                 "status": "completed",
#                 "gif_url": gif_url,
#                 "message": "Video successfully converted to GIF"
#             }
            
#         except Exception:
#             # GIF doesn't exist yet, check if task is still processing
#             return {
#                 "task_id": task_id,
#                 "status": "processing",
#                 "message": "Video conversion in progress"
#             }
            
#     except Exception as e:
#         logging.error(f"Error checking conversion status: {str(e)}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to check conversion status"
#         )

