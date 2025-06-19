from typing import TypeVar, Type, Any
from pydantic import BaseModel
from sqlalchemy import inspect

ModelT = TypeVar('ModelT', bound=BaseModel)

def model_to_response(db_model: Any, response_model: Type[ModelT]) -> ModelT:
    """Generic converter from SQLAlchemy model to Pydantic model"""
    mapper = inspect(db_model)
    fields = {column.key: getattr(db_model, column.key) 
              for column in mapper.attrs}
    return response_model(**fields)

