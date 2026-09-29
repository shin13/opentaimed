# Deployment — network requirements

This page lists what the network must allow for the shared HTTP service
(`docker compose up -d`, see the README "Deployment" section). Individual `uvx`
installs need the same outbound access, but nothing inbound.

Everything here was checked on 2026-09-28 unless a line says otherwise.

## Outbound (the `mcp` container → the internet)

The server calls three government hosts. All use HTTPS on TCP 443. None of them
redirected to another host when checked.

| Host | Agency | Used for | Size / timing |
|---|---|---|---|
| `mcp.fda.gov.tw` | TFDA 食藥署 | 仿單 text (`get_package_insert`, `check_insert_updates`) | Small, per query |
| `data.fda.gov.tw` | TFDA 食藥署 | Dataset 37 (licences) and 42 (appearance) | Downloaded daily, cached |
| `info.nhi.gov.tw` | NHI 健保署 | 健保用藥品項 (`get_nhi_drug_item`, `list_nhi_drug_items`) | 92 MB, over 120 s, updated monthly |

- Allow these by hostname if your firewall can. The IPs seen on 2026-09-28 were
  `210.69.111.168` (mcp), `210.69.110.23` (data) and `210.69.215.152` (info.nhi).
  They are not promised to stay the same.
- The container also needs working DNS for these names. Docker's default bridge
  network uses the host's resolver.
- If a host is blocked, its tools return an error to the client. They do not
  fall back to made-up data.
- GetDrugDoc needs all four query parameters. Without them it returns HTTP 500,
  which is not a network problem.

## NHI may refuse hosts outside Taiwan

`info.nhi.gov.tw` resets connections from many GitHub Actions runners (Azure,
outside Taiwan). TCP and TLS succeed, the request is sent, and then the
connection is reset after 3–4 s. The same request from Taiwan returns 200 in
under a second. It depends on the runner: four runner IPs were reset, and one
(on 2026-09-28) got 200 every time. See issue #113.

Why NHI does this is not known. It may block cloud or foreign IP ranges. That is
not proven. So **a deployment outside Taiwan (for example an AWS region abroad)
may lose the NHI tools** while the TFDA tools keep working.

Run this check **from the deployment host** before relying on it:

```bash
for url in \
  "https://mcp.fda.gov.tw/Serv/Query.asmx/GetDrugDoc?license=02021571&s_code=&startdate=2026/09/18&enddate=2026/09/27" \
  "https://data.fda.gov.tw/data/opendata/export/37/json" \
  "https://info.nhi.gov.tw/api/iode0010/v1/rest/dataset/A21030000I-E41001"; do
  curl -sS -o /dev/null -r 0-0 --max-time 20 -w "%{http_code} %{time_total}s  $url\n" "$url"
done
```

Every line should start with `200`. A `curl: (56) ... Connection reset by peer`
on the NHI line means this host is affected. Run it a few times, because the
resets are not seen on every attempt from every IP.

## Outbound through a proxy

The server uses `httpx`, which reads the standard proxy variables. Put them in
`.env`, which the compose file loads into the container:

```bash
HTTPS_PROXY=http://proxy.example.local:3128
NO_PROXY=localhost,127.0.0.1
```

**Always set `NO_PROXY` when you set a proxy.** The container healthcheck calls
`http://localhost:8765/health`. Without `NO_PROXY`, an `HTTP_PROXY` setting sends
that call to the proxy too, and the container is marked unhealthy.

## TLS inspection by a proxy

The server trusts the CA list bundled with Python's `certifi` package. It does
**not** use the operating system's certificate store. So adding the hospital CA
to the OS is not enough.

If your proxy re-signs HTTPS traffic, mount a PEM bundle that holds the public
roots **and** the hospital CA, and point to it in `.env`:

```bash
SSL_CERT_FILE=/certs/ca-bundle.pem
```

The symptom without this is an SSL certificate-verify error on every upstream
call. Do not work around it by turning off verification.

## Inbound (clients → the service)

- Only the Caddy container publishes a port: `443`. Bind it to the hospital LAN
  IP when you deploy.
- The `mcp` container publishes no port. It is reachable only through Caddy on
  the internal Docker network.
- TLS for clients ends at Caddy, using the cert and key in `./certs/`.
- Do not expose the service to the public internet (security invariant #8).
- Clients connect to `https://<your-host>/mcp/` (keep the trailing slash).

## Slow links and first start

On HTTP startup the service downloads all three datasets before it reports
healthy. On an empty cache volume the NHI download alone takes about 120 s. On a
slow or proxied link, raise the healthcheck `start_period` in
`docker-compose.yml` from 180 s toward 600 s. Keep the `fda-cache` volume so
later restarts skip the download.
