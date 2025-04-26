# Web Content Translator and Summarizer

## Overview

This service is a Flask application deployed on Google Cloud Run that provides automatic translation and summarization of web content. It takes text content and a URL as input, processes it using Google's Gemini AI model, and creates a formatted Google Document with the results.

## Features

- **Text Translation**: Translates provided text content from any language to Japanese
- **Content Summarization**: Formats the translated content as bullet points for easy reading
- **Code Block Preservation**: Maintains code blocks with proper formatting
- **Keyword Extraction**: Identifies and lists important nouns and technical terms
- **Document Creation**: Automatically generates a Google Document with formatted content
- **Document Sharing**: Shares the created document with a specified admin user

## Architecture

- **Runtime**: Python 3.11
- **Server**: Flask with Gunicorn
- **Cloud Infrastructure**: Google Cloud Run
- **APIs Used**:
  - Google Vertex AI (Gemini 2.0 Flash Lite)
  - Google Docs API
  - Google Drive API

## Prerequisites

- Google Cloud Project
- Required API access and permissions:
  - Vertex AI API
  - Google Docs API
  - Google Drive API
- Service account with appropriate roles:
  - Vertex AI User
  - Google Docs and Drive access

## Environment Variables

- `GCP_PROJECT_ID`: Your Google Cloud Project ID
- `GCP_REGION`: Google Cloud region (defaults to 'us-central1')
- `PORT`: Port for the server (defaults to 8080)

## Deployment

### Using Docker

The service is containerized using Docker:

```
FROM python:3.11-slim

ENV PYTHONUNBUFFERED True
ENV APP_HOME /app
WORKDIR $APP_HOME

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
```

### Deployment Commands

Deploy to Google Cloud Run:

```bash
gcloud run deploy web-content-processor \
  --source . \
  --platform managed \
  --region [YOUR_REGION] \
  --set-env-vars="GCP_PROJECT_ID=[YOUR_PROJECT_ID]" \
  --service-account=[YOUR_SERVICE_ACCOUNT] \
  --allow-unauthenticated
```

## API Usage

### Endpoint

`POST /`

### Request Body

```json
{
  "text": "Content to translate and summarize",
  "url": "https://source-url-of-content.com"
}
```

### Response

```json
{
  "document_url": "https://docs.google.com/document/d/..."
}
```

### Error Response

```json
{
  "error": "Error message"
}
```

## Document Format

The generated Google Document includes:

1. **Summary Section**: Translated text formatted as bullet points
2. **Code Blocks**: Preserved code snippets with monospace formatting
3. **URL Section**: Source URL with page title and hyperlink
4. **Keywords Section**: List of relevant nouns and technical terms

## Dependencies

Required Python packages (create a requirements.txt with these):

- flask
- google-cloud-aiplatform
- google-api-python-client
- google-auth
- vertexai
- requests
- beautifulsoup4

## Security Notes

- The service shares created documents with a hardcoded admin email
- Authentication to Google services is handled via the runtime service account
- Ensure proper IAM permissions are set for the service account

## Error Handling

The service includes error handling for:
- API initialization failures
- Gemini API processing errors
- Google Docs/Drive API errors
- JSON parsing failures