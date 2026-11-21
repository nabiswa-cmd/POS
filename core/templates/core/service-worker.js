"use strict";

var CACHE_NAME = "winespos-offline-v1";
var OFFLINE_URL = "/offline/";

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE_NAME).then(function (cache) {
    return cache.add(OFFLINE_URL);
  }).then(function () {
    return self.skipWaiting();
  }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (key) {
      return key.indexOf("winespos-offline-") === 0 && key !== CACHE_NAME;
    }).map(function (key) { return caches.delete(key); }));
  }).then(function () {
    return self.clients.claim();
  }));
});

self.addEventListener("fetch", function (event) {
  if (event.request.method !== "GET" || event.request.mode !== "navigate") return;
  var requestUrl = new URL(event.request.url);
  if (requestUrl.origin !== self.location.origin) return;

  function offlinePage() {
    return caches.match(OFFLINE_URL).then(function (response) {
      return response || Response.error();
    });
  }

  event.respondWith(fetch(event.request).then(function (response) {
    if ([502, 503, 504].indexOf(response.status) !== -1) return offlinePage();
    return response;
  }).catch(offlinePage));
});
