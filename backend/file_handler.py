import os
import shutil
import hashlib
import mimetypes
import io
from pathlib import Path
from typing import Optional, Dict, Any, BinaryIO
from datetime import datetime
import aiofiles
from PIL import Image
import PyPDF2
from docx import Document
import openpyxl
import asyncio
from fastapi import UploadFile, HTTPException
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FileHandler:
    """Handle file uploads, downloads, and processing"""
    
    def __init__(self, upload_dir: str = "./uploads", download_dir: str = "./downloads"):
        self.upload_dir = Path(upload_dir)
        self.download_dir = Path(download_dir)
        
        # Create directories if they don't exist
        self.upload_dir.mkdir(exist_ok=True)
        self.download_dir.mkdir(exist_ok=True)
        
        # File size limits (in bytes)
        self.max_file_size = 100 * 1024 * 1024  # 100 MB
        
        # Allowed MIME types
        self.allowed_types = {
            'image': ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
            'document': ['application/pdf', 'application/msword', 
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        'text/plain', 'text/markdown'],
            'spreadsheet': ['application/vnd.ms-excel',
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
            'audio': ['audio/wav', 'audio/mpeg', 'audio/mp3', 'audio/ogg'],
            'archive': ['application/zip', 'application/x-tar', 'application/x-gzip']
        }
        
        # File processing functions
        self.processors = {
            'application/pdf': self.process_pdf,
            'image/jpeg': self.process_image,
            'image/png': self.process_image,
            'text/plain': self.process_text,
        }
    
    async def upload_file(self, file: UploadFile, session_id: str) -> Dict[str, Any]:
        """Upload and process a file"""
        # Validate file size
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
        
        if size > self.max_file_size:
            raise HTTPException(400, f"File too large. Max {self.max_file_size / 1024 / 1024} MB")
        
        # Detect MIME type
        sample = await file.read(1024)
        await file.seek(0)
        try:
            import magic
            mime = magic.from_buffer(sample, mime=True)
        except Exception:
            mime = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        
        # Validate file type
        if not self.is_allowed_type(mime):
            raise HTTPException(400, f"File type {mime} not allowed")
        
        # Generate unique filename
        file_hash = hashlib.md5()
        content = await file.read()
        file_hash.update(content)
        await file.seek(0)
        
        extension = Path(file.filename).suffix
        unique_name = f"{session_id}_{file_hash.hexdigest()}{extension}"
        file_path = self.upload_dir / unique_name
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            await f.write(content)
        
        # Process file content
        processed_content = await self.process_file(content, mime)
        
        # Store metadata
        file_metadata = {
            "file_id": file_hash.hexdigest(),
            "original_name": file.filename,
            "stored_name": unique_name,
            "path": str(file_path),
            "size": size,
            "mime_type": mime,
            "upload_time": datetime.now().isoformat(),
            "session_id": session_id,
            "processed_content": processed_content
        }
        
        # Save metadata
        metadata_path = file_path.with_suffix('.meta.json')
        async with aiofiles.open(metadata_path, 'w') as f:
            import json
            await f.write(json.dumps(file_metadata))
        
        logger.info(f"File uploaded: {file.filename} for session {session_id}")
        
        return file_metadata
    
    def is_allowed_type(self, mime_type: str) -> bool:
        """Check if MIME type is allowed"""
        for category, types in self.allowed_types.items():
            if mime_type in types:
                return True
        return False
    
    async def process_file(self, content: bytes, mime_type: str) -> Optional[str]:
        """Process file content and extract text for the agent"""
        if mime_type in self.processors:
            return await self.processors[mime_type](content)
        return None
    
    async def process_pdf(self, content: bytes) -> str:
        """Extract text from PDF"""
        try:
            from io import BytesIO
            pdf_file = BytesIO(content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text = []
            for page in pdf_reader.pages:
                text.append(page.extract_text())
            
            return "\n".join(text)
        except Exception as e:
            logger.error(f"PDF processing error: {e}")
            return f"[PDF could not be processed: {e}]"
    
    async def process_image(self, content: bytes) -> str:
        """Analyze image and return description"""
        try:
            image = Image.open(io.BytesIO(content))
            
            # Basic image info
            info = f"Image: {image.width}x{image.height} pixels, {image.mode} mode"
            
            # For actual image analysis, you'd integrate with an LLM or vision API
            # This returns basic metadata
            return info
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return f"[Image could not be processed: {e}]"
    
    async def process_text(self, content: bytes) -> str:
        """Process text file"""
        try:
            return content.decode('utf-8')
        except:
            return content.decode('latin-1')
    
    async def download_file(self, file_id: str, session_id: str) -> Optional[Path]:
        """Prepare file for download"""
        # Find file by ID
        for file_path in self.upload_dir.glob(f"{session_id}_*.meta.json"):
            async with aiofiles.open(file_path, 'r') as f:
                import json
                metadata = json.loads(await f.read())
                if metadata['file_id'] == file_id:
                    original_path = Path(metadata['path'])
                    if original_path.exists():
                        # Create copy for download
                        download_path = self.download_dir / f"{session_id}_{metadata['original_name']}"
                        shutil.copy2(original_path, download_path)
                        return download_path
        
        return None
    
    async def list_user_files(self, session_id: str) -> list:
        """List all files uploaded by user"""
        files = []
        for meta_path in self.upload_dir.glob(f"{session_id}_*.meta.json"):
            async with aiofiles.open(meta_path, 'r') as f:
                import json
                metadata = json.loads(await f.read())
                files.append({
                    "file_id": metadata['file_id'],
                    "name": metadata['original_name'],
                    "size": metadata['size'],
                    "upload_time": metadata['upload_time']
                })
        return files
    
    async def delete_file(self, file_id: str, session_id: str) -> bool:
        """Delete a file"""
        for file_path in self.upload_dir.glob(f"{session_id}_*.meta.json"):
            async with aiofiles.open(file_path, 'r') as f:
                import json
                metadata = json.loads(await f.read())
                if metadata['file_id'] == file_id:
                    # Delete actual file
                    original_path = Path(metadata['path'])
                    if original_path.exists():
                        original_path.unlink()
                    # Delete metadata
                    file_path.unlink()
                    return True
        return False

# Singleton instance
file_handler = FileHandler()