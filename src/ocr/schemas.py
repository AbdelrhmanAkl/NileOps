from pydantic import BaseModel, Field

class FrontIdData(BaseModel):
    name:str = Field(description=("the name combined: First name and the remaining"))
    nid:int = Field(description=("the 14 number represinting the national id"))
    dob:str = Field(description=("date of birth"))
    location:str = Field(description=("the location on the id"))