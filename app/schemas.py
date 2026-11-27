from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

Amount = Annotated[Decimal, Field(max_digits=24, decimal_places=8, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[0-9]+$", max_length=40)]
DateText = Annotated[str, Field(max_length=30)]
Status = Annotated[str, Field(min_length=1, max_length=80)]


class Item(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: Identifier
    description: str = Field(max_length=500)
    quantity: Amount
    package: str = Field(min_length=1, max_length=80)
    total: Amount | None = None
    unit_value: Amount | None = None
    category: str | None = Field(default=None, max_length=200)

    @field_serializer("quantity", "total", "unit_value", when_used="json")
    def fixed_decimal(self, value):
        return format(value, "f") if value is not None else None


class Invoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: str = Field(pattern=r"^[0-9]+$", max_length=20)
    series: str = Field(pattern=r"^[0-9]+$", max_length=10)


class Record(BaseModel):
    code: Identifier
    origin: str = Field(max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    load: str = Field(max_length=80)
    reason: str = Field(max_length=200)
    included_at: DateText | None = None
    status: Status
    approved_at: DateText | None = None
    received_at: DateText | None = None
    returned_at: DateText | None = None
    bdv: str | None = Field(default=None, max_length=40)
    is_test: bool | None = None
    invoices: list[Invoice] = Field(default_factory=list, max_length=10000)
    source: Literal["clipboard", "spreadsheet", "native"] = "clipboard"
    items: list[Item] = Field(default_factory=list, max_length=10000)

    @field_validator("destination")
    @classmethod
    def require_store(cls, value):
        if not value.strip():
            raise ValueError("Loja obrigatória.")
        return value


class Paste(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    target_code: Identifier | None = None
    target_store: str | None = Field(default=None, max_length=200)


class Spreadsheet(BaseModel):
    content_base64: str = Field(min_length=1, max_length=28_000_000)
    sheet: str | None = Field(default=None, max_length=31)
    skip_invalid: bool = False
    mapping: dict[str, str] | None = None


class InventoryItem(BaseModel):
    code: Identifier
    quantity: Amount
    value: Amount | None = None


class InventoryRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=200)
    items: list[InventoryItem] = Field(min_length=1, max_length=10000)


class StatusUpdate(BaseModel):
    status: Status
    expected_status: Status
