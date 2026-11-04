from pydantic import BaseModel, Field


class FrontIdData(BaseModel):
    name: str = Field(description="the name combined: First name and the remaining")
    nid: int = Field(description="the 14 number representing the national id")
    dob: str = Field(description="date of birth")
    location: str = Field(description="the location on the id")


class BackIdData(BaseModel):
    nid: int = Field(description="the 14 number representing the national id")
    gender: str = Field(description="gender (e.g. ذكر or أنثى)")
    religion: str = Field(description="religion (e.g. مسلم or مسيحي)")
    marital_status: str = Field(description="marital status (e.g. اعزب, متزوج, مطلق, ارمل)")
    expire_date: str = Field(description="expiration date (e.g. YYYY/MM/DD)")
