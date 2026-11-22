# Smooth Prime 0.2.26: illustrated genre selection and real Grid continuation

Movies/TV focus opens a five-column illustrated chooser using existing bundled
category graphics. Select a genre or All to show content; Down/Enter enters the
grid. Menu: Movies, TV, In Progress, Extras, Purchases, Search, Settings, Sign out.
Extras groups the actual /sports and /addons catalogs. Confirmed Prime inclusion
and additional-subscription/event availability are labelled without claiming all
sports require extra payment. No purchasing flow is added.

Fantasy has two observed pagination failures: See more anchors include decorative
SVG Link Arrow text, and their single Grid uses tokens rather than rel=next.
The explicit data-testid/aria-label now identify the movie/TV link. A single
page-owned Grid declares target, token, start index, page type/id and capability.
Those fields are posted to the same public read-only PaginateCollection protocol
used by Amazon's published web client. Tokens are never decoded or reconstructed.
Subsequent requests follow hasMoreItems/startIndex/pagination.serviceToken; a
provider-declared end is obeyed. Carousel tokens are never promoted to page state.

Real public production-reader requests returned 20 Fantasy cards plus 40 distinct
additional cards, of which 15 and 22 respectively were confirmed Prime movies.
Amazon declared an end in that anonymous response. Comedy also returned 40 new
cards. This confirms the actual provider protocol/parser, not signed-in Kodi UI
or exhaustive platform coverage. First two pages warm; later lookahead starts
early and preserves selection. Structured real-run diagnostics include request
IDs, timings, status/routes, parser/request fields and provider end state, with
no token/cookie/password/account values. All authentication/media/playback, title
layout, description cleanup and original skin image/font bytes are unchanged.
