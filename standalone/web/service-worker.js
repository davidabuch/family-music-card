const CACHE="family-music-approved-icons-v2-dynamic-grouping-v4";
const ASSETS=["/","/styles.css","/app.js","/manifest.webmanifest","/icons/approved-v2/icon-16.png","/icons/approved-v2/icon-32.png","/icons/approved-v2/icon-180.png","/icons/approved-v2/icon-192.png","/icons/approved-v2/icon-512.png","/favicon.ico","/apple-touch-icon.png?v=approved-v2","/apple-touch-icon-precomposed.png?v=approved-v2"];
self.addEventListener("install",(event)=>event.waitUntil(caches.open(CACHE).then((cache)=>cache.addAll(ASSETS))));
self.addEventListener("activate",(event)=>event.waitUntil(caches.keys().then((keys)=>Promise.all(keys.filter((key)=>key!==CACHE).map((key)=>caches.delete(key))))));
self.addEventListener("fetch",(event)=>{
  const url=new URL(event.request.url);
  if(url.origin!==self.location.origin||url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request,{cache:"no-store"}).then((response)=>{
    const copy=response.clone();
    caches.open(CACHE).then((cache)=>cache.put(event.request,copy));
    return response;
  }).catch(()=>caches.match(event.request)));
});
