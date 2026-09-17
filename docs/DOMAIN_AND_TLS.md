# M3: Domain, TLS, and Reverse Proxy Configuration

## 1. Overview
This document outlines the domain configuration, TLS termination, and reverse proxy setup for the production Intelliview Orchestrator deployment.

## 2. Infrastructure Architecture
* **Frontend:** Cloudflare Pages (Next.js)
* **Backend:** Render (FastAPI Web Service)
* **Database:** Neon PostgreSQL
* **Cache/Queue:** Managed Redis

## 3. Reverse Proxy & API Routing
Reverse proxy routing is handled at the Cloudflare Pages edge using the `_redirects` configuration rule (`frontend/public/_redirects`).

* **Frontend Route:** `https://<your-production-domain>/` -> Serves Cloudflare Pages Next.js bundle.
* **API Proxy Route:** `https://<your-production-domain>/api/*` -> Proxied to `https://<render-backend-host>/api/:splat` (HTTP 200 rewrite).

## 4. TLS / HTTPS Termination
* **Edge TLS:** Managed automatically by Cloudflare Pages using free automated Let's Encrypt / Cloudflare Managed Certificates.
* **Backend TLS:** Render manages origin HTTPS termination automatically on `*.onrender.com` / custom domains.
* **SSL/TLS Encryption Mode:** Cloudflare SSL/TLS set to **Full (Strict)** to guarantee end-to-end encryption between the Cloudflare Edge and the Render origin service.

## 5. DNS Records Configuration
| Type | Name | Target / Value | Proxy Status |
| :--- | :--- | :--- | :--- |
| CNAME | `@` / `app` | `<project-name>.pages.dev` | Proxied (Orange Cloud) |
| CNAME | `api` | `<backend-service>.onrender.com` | DNS Only / Proxied |