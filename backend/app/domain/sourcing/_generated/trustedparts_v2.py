# AUTO-GENERATED FILE - DO NOT EDIT. Run `make regen-tp-models` from the repository root.

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PartSpecification(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Key: Annotated[str | None, Field(description="The specification name")] = None
    Value: Annotated[str | None, Field(description="The specification value")] = None


class Price(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Amount: Annotated[float | None, Field(description="Price amount")] = None
    FormattedAmount: Annotated[
        str | None, Field(description="Formatted price amount")
    ] = None
    Quantity: Annotated[float | None, Field(description="Quantity for price")] = None
    Text: Annotated[str | None, Field(description="Text representation of price")] = (
        None
    )


class ProductPackageType(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    MinimumOrderQuantity: Annotated[
        int | None, Field(description="Minimum order quantity")
    ] = None
    PackageType: Annotated[str | None, Field(description="Package type")] = None


class ProductPricing(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    CurrencyCode: Annotated[
        str | None,
        Field(description='Currency code for the pricing amounts (e.g., "USD").'),
    ] = None
    MinimumQuantity: Annotated[
        float | None, Field(description="Minimum order quantity for the price.")
    ] = None
    Prices: Annotated[
        list[Price] | None, Field(description="List of prices for specific quantities.")
    ] = None
    QuantityMultiple: Annotated[
        float | None, Field(description="Quanitity multiple")
    ] = None


class RohsCompliance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Description: Annotated[
        str | None, Field(description="Description of RoHS compliance")
    ] = None
    IsCompliant: Annotated[
        bool | None, Field(description="Is the product RoHS compliant?")
    ] = None
    Region: Annotated[
        str | None, Field(description="Region for the RoHS compliance information")
    ] = None


class SearchApiLink(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Type: Annotated[
        str | None, Field(description='Type of link (e.g., "datasheet", "distributor")')
    ] = None
    Url: Annotated[str | None, Field(description="URL of the link.")] = None


class SearchQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Manufacturers: Annotated[
        list[str] | None,
        Field(
            description='The manufacturers that will be searched.\nAdd this section to limit the search results to one more more manufacturers.\nOmit this section to search all authorized manufacturers.\nOur supported manufacturers can be found <a href="https://www.trustedparts.com/docs/api/manufacturers-list/">here</a>.',
            examples=[
                [
                    "enter a manufacturer name or remove the optional Manufacturers parameter"
                ]
            ],
        ),
    ] = None
    SearchToken: Annotated[
        str,
        Field(
            description="Enter the search term. The search term must be at least two characters and no more than 100 characters.",
            examples=["bav99"],
            max_length=100,
            min_length=2,
        ),
    ]


class StockInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Availability: Annotated[
        str | None,
        Field(
            description='Contains a message about stock availability. For instance, "In Stock".'
        ),
    ] = None
    QuantityOnHand: Annotated[
        float | None,
        Field(
            description="Indicates number of parts currently on hand. Will be null if we are not going to disclose the exact number."
        ),
    ] = None


class InventoryApiRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    ApiKey: Annotated[
        str | None,
        Field(
            description="Enter your assigned API key.\nThe assigned API key can be found on the API Credentials page.\n            \nAlternatively, you may provide the API key in the X-Api-Key header and omit this parameter\nfrom the request.",
            examples=["enter my api key"],
        ),
    ] = None
    CompanyId: Annotated[
        str | None,
        Field(
            description="Company identifier assigned to your account.\nThe assigned company identifier can be found on the API Credentials page.\n            \nThis property is deprecated, no longer required and may be omitted.",
            examples=["enter my company id (deprecated)"],
        ),
    ] = None
    CountryCode: Annotated[
        str | None,
        Field(
            description="Enter a two-character ISO code or leave blank for geolocation.\n\nLeave this field blank if you would like us to geolocate the SourceIp parameter\nand use the country that the IP address is from. If you use the TrustedParts.com API\nto provide results on a website, this allows you to provide regional results to each\nuser without any effort on your part.",
            examples=["US"],
        ),
    ] = None
    CurrencyCode: Annotated[
        str | None,
        Field(description="Enter a three-character ISO currency code. (Optional)"),
    ] = "USD"
    Distributors: Annotated[
        list[str] | None,
        Field(
            description='The distributors that will be included in this search.\nAdd this section to limit the search results to one or more distributors.\nOmit this section to search all authorized distributors.\nOur supported distributors can be found <a href="https://www.trustedparts.com/docs/api/distributors-list/">here</a>.',
            examples=[
                [
                    "enter a distributor name or remove the optional Distributors parameter"
                ]
            ],
        ),
    ] = None
    ExactMatch: Annotated[
        bool | None,
        Field(
            description="Set to true to view parts that match the search term exactly.\nSet to false to include partial match results.\nNote: Partial match results are only available for single part number requests.\nMost punctuation and spacing are ignored when determining what is an exact match.\nFor instance, BAT-54C is an exact match for BAT54C.",
            examples=[False],
        ),
    ] = False
    InStockOnly: Annotated[
        bool | None,
        Field(
            description="Set to true to view in-stock results only. (Optional)",
            examples=[False],
        ),
    ] = False
    IsCrawler: Annotated[
        bool | None,
        Field(
            description="Set to true if the end user is a crawler or bot (e.g. Google, Bing, Baidu).\nSet to false if the end user is not a crawler or if undetermined.",
            examples=[False],
        ),
    ] = None
    LanguageCode: Annotated[
        str | None,
        Field(
            description="Enter the ISO language code for part specification translations or leave blank for English.\n\nSupported values: \n\nde, en, es, fr, it, pt, ja, zh-hans, zh-hant",
            examples=["en"],
        ),
    ] = "en"
    Queries: Annotated[
        list[SearchQuery],
        Field(
            description="The part number(s) and manufacturer(s) you want to search for.\n\nAdd a separate query for each part number search.\n\nNote: If you enter multiple part numbers, an exact match search will be performed\nfor each part number. Partial match results are only available for single part number requests."
        ),
    ]
    SourceIp: Annotated[
        str | None,
        Field(
            description="Provide the IP (Internet Protocol) address of the end user who will view the results.\nPlease ensure the accuracy of this value so that TrustedParts.com can verify\nthe identity of users who receive results. \n<b>Geolocation:</b> When the CountryCode parameter is omitted or left blank, the\nSourceIp parameter will be used to geolocate the request and provide regional results.",
            examples=["enter my IP address"],
        ),
    ] = None
    UseCachedData: Annotated[
        bool | None,
        Field(
            description="If this parameter is not supplied or set to false, the TrustedParts.com API\nwill provide results based on real-time stock and pricing services provided\nby participating distributors. This is the preferred setting since it yields\nthe most accurate results. However, if you need extremely fast responses or\nneed to submit a high volume of requests in a short period of time, you may\nset this parameter to true. This will direct the TrustedParts.com API to use\nan optimized search process that retrieves results from locally-cached data\ninstead of real-time data.",
            examples=[False],
        ),
    ] = False
    UserAgent: Annotated[
        str | None,
        Field(
            description="Provide the user agent of the requesting application or end user's browser.\nPlease ensure the accuracy of this value so that TrustedParts.com can verify\nthe identity of users who receive results.",
            examples=["enter my application user agent"],
        ),
    ] = None


class ProductCompliance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    RoHS: Annotated[
        list[RohsCompliance] | None,
        Field(
            description="Contains regional RoHS compliance information for products."
        ),
    ] = None


class InventoryDistributorResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Compliance: ProductCompliance | None = None
    Description: Annotated[str | None, Field(description="The part's description.")] = (
        None
    )
    DistributorPartNumber: Annotated[
        str | None, Field(description="The distributor part number.")
    ] = None
    Links: Annotated[
        list[SearchApiLink] | None,
        Field(
            description="Links. For example, datasheet URL or distributor product URL."
        ),
    ] = None
    Packaging: Annotated[
        list[ProductPackageType] | None, Field(description="Packaging information.")
    ] = None
    Pricing: ProductPricing | None = None
    Stock: StockInfo | None = None


class PartDistributor(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    DistributorResults: Annotated[
        list[InventoryDistributorResult] | None,
        Field(description="The list of matched manufacturer parts."),
    ] = None
    Id: Annotated[int | None, Field(description="Distributor Id")] = None
    Name: Annotated[str | None, Field(description="Distributor name.")] = None


class InventoryPartResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    Distributors: Annotated[
        list[PartDistributor] | None,
        Field(description="The distributors with matches."),
    ] = None
    IsAffectedByTariff: Annotated[
        bool | None,
        Field(
            description="Have TrustedParts.com distributors indicated that the part is affected by tariffs in the United States?"
        ),
    ] = None
    LifecycleRisk: Annotated[
        str | None,
        Field(
            description="Lifecycle risk of a matching part.\n            \nPlease email user-requests@trustedparts.com to see if our policies allow this information\nto be provided to you."
        ),
    ] = None
    Manufacturer: Annotated[
        str | None, Field(description="The manufacturer name for the result.")
    ] = None
    ManufacturerId: Annotated[
        int | None,
        Field(description="The TrustedParts manufacturer ID for the result."),
    ] = None
    PartNumber: Annotated[
        str | None, Field(description="The manufacturer part number for the result.")
    ] = None
    ProductUrl: Annotated[
        str | None,
        Field(description="The TrustedParts.com product page for the result."),
    ] = None
    Specifications: Annotated[
        list[PartSpecification] | None,
        Field(
            description="Specifications of a matching part.\n            \nPlease email user-requests@trustedparts.com to see if our policies allow this information\nto be provided to you."
        ),
    ] = None
    SupplyChainRisk: Annotated[
        str | None,
        Field(
            description="Supply Chain risk of a matching part.\n            \nPlease email user-requests@trustedparts.com to see if our policies allow this information\nto be provided to you."
        ),
    ] = None


class InventoryApiResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    CurrentDate: Annotated[
        AwareDatetime | None,
        Field(description="Date and time that results were returned."),
    ] = None
    ErrorMessage: Annotated[
        str | None,
        Field(description="A message regarding an error generated by the search."),
    ] = None
    Messages: Annotated[
        list[str] | None, Field(description="Messages regarding the search results.")
    ] = None
    OriginalRequest: InventoryApiRequest | None = None
    PartResults: Annotated[
        list[InventoryPartResult] | None,
        Field(description="The search results grouped by part number searched for."),
    ] = None
    ResponseTime: Annotated[
        str | None,
        Field(
            description="The amount of time it took for the API to respond with results."
        ),
    ] = None
