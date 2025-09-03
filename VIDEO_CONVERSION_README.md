# Video to GIF Conversion System

This system converts uploaded videos to GIF format using background processing with Dramatiq.

## Features

- **Video Upload**: Accepts video files up to 25MB and 2 minutes duration
- **Background Processing**: Uses Dramatiq for reliable background task processing
- **Appwrite Storage**: Stores both videos and GIFs in Appwrite cloud storage
- **Automatic Cleanup**: Removes video files after successful conversion
- **Status Tracking**: Provides task IDs for monitoring conversion progress

## Setup

### 1. Install Dependencies

```bash
uv add dramatiq
uv add appwrite
```

### 2. Environment Variables

Add these to your `.env` file:

```env
APPWRITE_ENDPOINT=https://fra.cloud.appwrite.io/v1
APPWRITE_PROJECT_ID=your_project_id
APPWRITE_API_KEY=your_api_key
APPWRITE_BUCKET_ID=your_bucket_id
```

### 3. Start Redis (Required for Dramatiq)

```bash
# If using Docker
docker run -d -p 6379:6379 redis:alpine

# Or install Redis locally
sudo apt-get install redis-server
```

### 4. Test Dramatiq Setup

```bash
# Test that Dramatiq can queue tasks
python test_dramatiq.py

# Expected output:
# ✅ Dramatiq task queued successfully!
# Task ID: [some-uuid]
# Task status: pending
```

## Usage

### 1. Start the Dramatiq Worker

```bash
python run_dramatiq_worker.py
```

### 2. Upload and Convert Video

```bash
curl -X POST "http://localhost:8000/api/items/convert-video-to-gif" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -F "video=@your_video.mp4"
```

**Response:**

```json
{
  "task_id": "abc123-def456",
  "status": "processing",
  "message": "Video uploaded and conversion started in background",
  "video_filename": "uuid-video.mp4",
  "gif_filename": "uuid-video.gif"
}
```

### 3. Check Conversion Status

```bash
curl "http://localhost:8000/api/items/conversion-status/abc123-def456?gif_filename=uuid-video.gif" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Response (Processing):**

```json
{
  "task_id": "abc123-def456",
  "status": "processing",
  "message": "Video conversion in progress"
}
```

**Response (Completed):**

```json
{
  "task_id": "abc123-def456",
  "status": "completed",
  "gif_url": "https://fra.cloud.appwrite.io/v1/storage/buckets/bucket_id/files/uuid-video.gif/view?project=project_id",
  "message": "Video successfully converted to GIF"
}
```

## API Endpoints

### POST `/api/items/convert-video-to-gif`

- **Purpose**: Upload video and start conversion
- **Input**: Video file (max 25MB, max 2 minutes)
- **Output**: Task ID and status

### GET `/api/items/conversion-status/{task_id}`

- **Purpose**: Check conversion status
- **Input**: Task ID and GIF filename
- **Output**: Current status and GIF URL if complete

## Workflow

1. **Upload**: Video is uploaded to Appwrite storage
2. **Queue**: Conversion task is queued in Dramatiq
3. **Process**: Background worker processes the video
4. **Convert**: Video is converted to GIF using moviepy
5. **Store**: GIF is uploaded to Appwrite storage
6. **Cleanup**: Original video is deleted from Appwrite
7. **Complete**: Task status is updated

## Benefits of Dramatiq

- **Reliability**: Built-in retry mechanisms
- **Scalability**: Can run multiple workers
- **Monitoring**: Task status tracking
- **Persistence**: Redis-backed queue
- **Error Handling**: Graceful failure management

## Error Handling

- **File Size**: Rejects videos over 25MB
- **Duration**: Automatically trims videos over 2 minutes
- **Format**: Validates video file types
- **Cleanup**: Removes failed uploads automatically
- **Logging**: Comprehensive error logging

## Monitoring

Check worker logs for:

- Task start/completion
- Conversion progress
- Error details
- Cleanup operations

## Performance

- **Background Processing**: Non-blocking video uploads
- **Efficient Conversion**: Optimized GIF settings (15 FPS)
- **Memory Management**: Automatic cleanup of temporary files
- **Storage Optimization**: Videos are deleted after conversion

## Integration with Product Creation

### Using Videos in Product Creation

The video conversion system is now integrated with the product creation workflow. When creating products, you can upload both images and videos:

```bash
# Create a product with images and videos
curl -X POST "http://localhost:8000/api/products" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -F "name=Product Name" \
  -F "description=Product Description" \
  -F "price=1000" \
  -F "stock=10" \
  -F "category_id=uuid-here" \
  -F "images=@image1.jpg" \
  -F "images=@video1.mp4" \
  -F "images=@image2.png"
```

### How It Works

1. **Mixed Upload**: The system accepts both images and videos in the same `images` array
2. **Automatic Detection**: Files are automatically detected as images or videos based on content type
3. **Background Processing**: Videos are converted to GIFs in the background using Dramatiq
4. **Placeholder URLs**: Initially, videos get placeholder URLs like `video_processing:filename:task_id`
5. **Automatic Updates**: When conversion completes, ItemImage records are automatically updated with final GIF URLs

### Processing Completed Conversions

#### Manual Processing

```bash
# Process all completed video conversions manually
curl -X POST "http://localhost:8000/api/items/process-completed-videos" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### Automatic Processing

The system includes a cron job that automatically processes completed conversions every few minutes.

### Monitoring Video Conversions

You can check the status of individual video conversions:

```bash
curl "http://localhost:8000/api/items/conversion-status/{task_id}?gif_filename={filename}" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### Database Schema

The system uses the existing `ItemImage` table:

- **Regular Images**: Store direct URLs to S3/Appwrite
- **Pending Videos**: Store placeholder URLs like `video_processing:filename:task_id`
- **Completed Videos**: Store final GIF URLs from Appwrite

### Redis Storage

Video conversion tasks are stored in Redis for tracking:

- **Key Format**: `video_conversions:{item_id}`
- **Expiration**: 1 hour
- **Completion Tracking**: `completed_video_conversion:{gif_filename}`

### Error Handling

- **Failed Conversions**: Videos are automatically cleaned up from Appwrite
- **Database Updates**: Failed ItemImage updates are logged and can be retried
- **Fallback Processing**: System checks both Redis and conversion status for reliability
