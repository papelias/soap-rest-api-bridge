# SOAP fixture provenance

`countryinfo.wsdl` is a public WSDL snapshot fetched on 2026-11-01 from:

http://webservices.oorsprong.org/websamples.countryinfo/CountryInfoService.wso?WSDL

It is self-contained and lets Zeep serialize requests and parse responses offline.
`catalog.wsdl` is the authored fixture for catalog extraction edge cases.

The XML envelopes in `soap/` are authored test examples matching the public SOAP
1.1 schema, not recordings or authoritative country data. The list deliberately
contains a synthetic second country to test multiple items. The fault is also
synthetic: the public WSDL does not declare faults. Structured fixtures exercise
currency records and nested language lists. Tests feed these envelopes through
the adapter's real Zeep response parser using HTTPX MockTransport.
