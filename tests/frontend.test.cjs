const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const root = path.resolve(__dirname, '..');

function environment() {
  const clock = {now: 0};
  const timers = new Map();
  let timerId = 0;
  class DateMock extends Date { static now() { return clock.now; } }
  const timer = (fn, ms) => {
    const id = ++timerId;
    timers.set(id, fn);
    if (ms <= 1500) queueMicrotask(() => {
      if (timers.delete(id)) { clock.now += ms; fn(); }
    });
    return id;
  };
  const element = {textContent: '', classList: {add(){}, remove(){}},
    setAttribute(){}, removeAttribute(){}};
  const context = vm.createContext({
    Date: DateMock, clock, AbortController, crypto: {randomUUID}, console: {info(){}, warn(){}, error(){}},
    localStorage: {getItem(){return null}, setItem(){}}, sessionStorage: {setItem(){}},
    document: {querySelector(){return element}, querySelectorAll(){return []}, documentElement:{dataset:{}}},
    window: {setTimeout: timer, clearTimeout: id => timers.delete(id), customCards: []},
    setInterval(){}, clearInterval(){}, clearTimeout: id => timers.delete(id),
    HTMLElement: class {attachShadow(){this.shadowRoot = {getElementById(){return element}}}},
    customElements: {get(){}, define(){}}, navigator: {},
  });
  return {context, timers, run: code => vm.runInContext(code, context)};
}

function standalone() {
  const env = environment();
  env.run(fs.readFileSync(path.join(root, 'standalone/web/app.js'), 'utf8').replace(/\nboot\(\);\s*$/, ''));
  env.run(`
    showToast = message => { state.lastToast = message };
    renderGroupOverlay = () => {};
    updateGroupOverlay = () => {};
    state.players = [
      {player_id:'A',provider:'sonos',type:'player',available:true,group_members:[],synced_to:null},
      {player_id:'B',provider:'sonos',type:'player',available:true,group_members:[],synced_to:null}
    ];
    // UI grouping accepts physical Sonos IDs; use the real prefix.
    state.players.forEach(p => p.player_id = 'RINCON_' + p.player_id);
    state.selectedPlayerId = 'RINCON_A';
    state.commandCount = 0;
    api = async () => {state.commandCount++; return {accepted:true,confirmation_timeout_ms:30000}};
  `);
  return env;
}

test('standalone delayed join after nine seconds confirms without duplicate commands', async () => {
  const {run} = standalone();
  run(`refreshState = async () => {
    if (Date.now() >= 9000) {
      state.players[0].group_members=['RINCON_A','RINCON_B'];
      state.players[1].synced_to='RINCON_A';
    }
    return true;
  }`);
  await run(`toggleGroupMember('RINCON_B')`);
  assert.equal(run('state.lastGroupChange.outcome'), 'confirmed');
  assert.equal(run('state.commandCount'), 1);
  assert.equal(run('state.lastToast'), undefined);
});

test('standalone rejected join stays visible and is never retried', async () => {
  const {run} = standalone();
  run(`api=async()=>{state.commandCount++;const e=new Error('not allowed');e.outcome='rejected';throw e};`);
  await run(`toggleGroupMember('RINCON_B')`);
  assert.equal(run('state.lastGroupChange.outcome'), 'rejected');
  assert.equal(run('state.lastToast'), 'not allowed');
  assert.equal(run('state.commandCount'), 1);
});

test('standalone unavailable confirmation never turns stale state into success; late state reconciles', async () => {
  const {run} = standalone();
  run(`state.players[0].group_members=['RINCON_A','RINCON_B'];
    state.players[1].synced_to='RINCON_A';
    refreshState=async()=>false;`);
  await run(`toggleGroupMember('RINCON_B')`); // leave requested; stale joined state remains
  assert.equal(run('state.lastGroupChange.outcome'), 'unknown');
  assert.match(run('state.lastToast'), /outcome unknown/);
  run(`state.players[0].group_members=[];state.players[1].synced_to=null;
    reconcileGroupChange(state.lastGroupChange);`);
  assert.equal(run('state.lastGroupChange.outcome'), 'confirmed');
});

test('standalone coordinator removal follows only observed successor, preserving a user selection change', async () => {
  const {run} = standalone();
  run(`state.players[0].group_members=['RINCON_A','RINCON_B'];
    state.players[1].synced_to='RINCON_A';
    refreshState=async()=>{
      state.players[0].group_members=[];
      state.players[1].synced_to=null;
      state.players[1].group_members=['RINCON_B'];return true;
    };`);
  await run(`toggleGroupMember('RINCON_A')`);
  assert.equal(run('state.selectedPlayerId'), 'RINCON_B');
  assert.equal(run('state.lastGroupChange.outcome'), 'confirmed');
});

function card() {
  const env = environment();
  env.run(fs.readFileSync(path.join(root, 'custom_components/family_music/www/family-music-card.js'),'utf8'));
  env.run(`
    const card=new FamilyMusicCard();
    card._config={config_entry_id:'ma-entry'};
    card._selectedPlayer='media_player.bedroom';
    card._updateNowPlaying=()=>{};card._renderNowOverlays=()=>{};
    card._diagnostics=[];
    card._reportPlaybackDiagnostic=async(...args)=>{card._diagnostics.push(args)};
    card.calls=[];
    card._hass={states:{
      'media_player.bedroom':{state:'idle',attributes:{}},
      'media_player.kitchen':{state:'playing',attributes:{}},
      'media_player.alexis':{state:'playing',attributes:{}}
    },callService:async(...args)=>{card.calls.push(args)}};
    const topology={available:true,leader_id:'A',speakers:[
      {entity_id:'media_player.kitchen',player_id:'A',coordinator:true,member:true,available:true},
      {entity_id:'media_player.bedroom',player_id:'B',coordinator:false,member:true,available:true},
      {entity_id:'media_player.alexis',player_id:'C',coordinator:false,member:true,available:true}
    ]};
    card._ws=async()=>topology;
  `);
  return env;
}

for (const selected of ['media_player.kitchen','media_player.bedroom','media_player.alexis']) {
  test(`HA play/pause resolves ${selected} to the MA coordinator using its actual state`, async () => {
    const {run}=card();
    run(`card._selectedPlayer=${JSON.stringify(selected)}`);
    await run('card._togglePlayback()');
    assert.equal(run('card.calls.length'),1);
    assert.equal(run('card.calls[0][1]'),'media_pause');
    assert.equal(run('card.calls[0][2].entity_id'),'media_player.kitchen');
    run(`card._hass.states['media_player.kitchen'].state='paused'`);
    await run('card._togglePlayback()');
    assert.equal(run('card.calls[1][1]'),'media_play');
    const ids=run(`card._diagnostics.filter(x=>x[0]==='service_start'||x[0]==='service_accepted').map(x=>x[4])`);
    assert.equal(ids[0],ids[1]);
    assert.ok(ids[0]);
  });
}

test('HA unavailable topology and unavailable coordinator fail closed', async () => {
  const {run}=card();
  run('card._ws=async()=>{throw new Error("topology offline")}');
  await run('card._togglePlayback()');
  assert.equal(run('card.calls.length'),0);
  assert.match(run('card._actionMessage'),/topology failed/);
  run(`card._ws=async()=>topology;card._hass.states['media_player.kitchen'].state='unavailable'`);
  await run('card._togglePlayback()');
  assert.equal(run('card.calls.length'),0);
});

test('HA service failure is visible, correlated, and never invokes a fallback', async () => {
  const {run}=card();
  run(`card._hass.callService=async(...args)=>{card.calls.push(args);throw new Error('service rejected')}`);
  await run('card._togglePlayback()');
  assert.equal(run('card.calls.length'),1);
  assert.match(run('card._actionMessage'),/service failed: service rejected/);
  assert.equal(run('card._optimisticPlayback'),null);
  assert.equal(run(`card._diagnostics.at(-1)[0]`),'service_error');
});

test('HA repeated taps cannot duplicate an in-flight command', async () => {
  const {run}=card();
  await run('Promise.all([card._togglePlayback(),card._togglePlayback()])');
  assert.equal(run('card.calls.length'),1);
});

test('HA permanent MA groups retain their valid destination without Sonos topology lookup', async () => {
  const {run}=card();
  run(`card._hass.states['media_player.bedroom']={state:'playing',attributes:{mass_player_type:'group'}};
    card._ws=async()=>{throw new Error('must not query physical topology')}`);
  await run('card._togglePlayback()');
  assert.equal(run('card.calls[0][2].entity_id'),'media_player.bedroom');
});

test('HA delayed grouping confirms after old 1.8-second window; missing topology never confirms leave', async () => {
  const {run}=card();
  run(`card._loadGroupTopology=async()=>Date.now()<9000?null:topology;
    card._pendingGroupChange={anchorEntityId:'media_player.kitchen',until:30000};`);
  assert.equal(await run(`card._waitForDynamicGroupConfirmation('B','join')`),true);
  run(`clock.now=0;card._loadGroupTopology=async()=>null;
    card._pendingGroupChange={anchorEntityId:'media_player.kitchen',until:30000};`);
  assert.equal(await run(`card._waitForDynamicGroupConfirmation('B','leave')`),false);
});

test('HA selection changed during topology resolution sends no command', async () => {
  const {run}=card();
  run(`card._ws=async()=>{card._selectedPlayer='media_player.kitchen';return topology}`);
  await run('card._togglePlayback()');
  assert.equal(run('card.calls.length'),0);
});

test('HA diagnostic command failure is observable and cannot interrupt playback', async () => {
  const {run}=card();
  run(`card._reportPlaybackDiagnostic=FamilyMusicCard.prototype._reportPlaybackDiagnostic;
    card._ws=async(type)=>{if(type==='family_music/client_diagnostic')throw new Error('unknown_command');return topology}`);
  await run('card._togglePlayback()');
  assert.equal(run('card.calls.length'),1);
  assert.match(run('card._diagnosticStatus'),/unavailable; local trace retained/);
});

function stateUI() {
  const env = standalone();
  env.run(`
    const nodes = new Map();
    document.querySelector = selector => {
      if (!nodes.has(selector)) nodes.set(selector, {textContent:'', value:0, dataset:{},
        listeners:{},addEventListener(type,handler){this.listeners[type]=handler},
        classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},
        getAttribute(){return ''},querySelectorAll(){return []}});
      return nodes.get(selector);
    };
    renderPlayerSelect=()=>{}; updateDestinationActivity=()=>{}; refreshNativeModes=async()=>{};
    state.players[0].state='paused';state.players[0].volume_level=10;
    state.queues=[{queue_id:'RINCON_A',state:'playing',current_item:{name:'Old queue'}}];
  `);
  return env;
}

test('stale playing queue cannot override paused player icon or toggle; unavailable disables control', () => {
  const {run} = stateUI();
  run('renderNow()');
  assert.equal(run('$("#playPause").textContent'), '▶');
  assert.equal(run('destinationIsPlaying(selectedPlayer())'), false);
  run(`state.players[0].state='playing';state.queues[0].state='paused';renderNow()`);
  assert.equal(run('$("#playPause").textContent'), '❚❚');
  run(`state.players[0].state='unavailable';renderNow()`);
  assert.equal(run('$("#playPause").disabled'), true);
});

test('group member follows only reciprocal coordinator observation; permanent MA group keeps own state', () => {
  const {run} = stateUI();
  run(`state.players[0].synced_to='RINCON_B';state.players[1].state='playing'`);
  assert.equal(run('observedPlayback()'), null);
  run(`state.players[1].group_members=['RINCON_A','RINCON_B']`);
  assert.equal(run('observedPlayback()'), 'playing');
  run(`state.players[0].type='group';state.players[0].state='paused'`);
  assert.equal(run('observedPlayback()'), 'paused');
});

test('overlapping polls share one read; post-command wait gets a fresh coalesced successor', async () => {
  const {run} = stateUI();
  run(`let reads=0;let release;
    api=()=>{reads++;return new Promise(resolve=>{release=()=>resolve({players:state.players,queues:[]})})};
    const first=refreshState();const poll=refreshState();
    const after=refreshState({afterCurrent:true});const after2=refreshState({afterCurrent:true});`);
  assert.equal(run('reads'), 1);
  run('release()');
  await run('first');
  assert.equal(run('reads'), 2);
  run('release()');
  await run('Promise.all([poll,after,after2])');
  assert.equal(run('reads'), 2);
});

test('delayed pause propagates without optimistic icon, duplicate commands or stale queue override', async () => {
  const {run} = stateUI();
  run(`state.players[0].state='playing';wire();renderNow();let reads=0;let mutations=0;
    api=async path=>{
      if(path==='/api/transport'){mutations++;return {accepted:true}}
      reads++;return {players:[{...state.players[0],state:reads>=3?'paused':'playing'}],queues:state.queues};
    };
    const pause=$('#playPause').listeners.click({currentTarget:$('#playPause')});
    const duplicate=$('#playPause').listeners.click({currentTarget:$('#playPause')});`);
  assert.equal(run('$("#playPause").textContent'), '❚❚'); // still the observed state
  await run('Promise.all([pause,duplicate])');
  assert.equal(run('mutations'), 1);
  assert.equal(run('reads'), 3);
  assert.equal(run('$("#playPause").textContent'), '▶');
  run(`api=async path=>path==='/api/transport'?(mutations++,{}):({players:[{...state.players[0],state:'playing'}],queues:[]})`);
  await run(`transport('play',$('#playPause'))`);
  assert.equal(run('mutations'), 2);
  assert.equal(run('$("#playPause").textContent'), '❚❚');
});

test('unconfirmed accepted transport is bounded, remains observed, and is visibly unknown', async () => {
  const {run} = stateUI();
  run(`let reads=0;api=async path=>path==='/api/transport'?{}:(reads++,{players:state.players,queues:state.queues})`);
  await run(`transport('play',$('#playPause'))`);
  assert.ok(run('reads') <= 7);
  assert.equal(run('observedPlayback()'), 'paused');
  assert.match(run('state.lastToast'), /not yet confirmed/);
  assert.equal(run('state.transportPending'), false);
});

test('failed or malformed observations cannot confirm commands or leave transport enabled', async () => {
  for (const expression of [`throw new Error('offline')`, 'return {}']) {
    const {run} = stateUI();
    run(`api=async()=>{${expression}}`);
    assert.equal(await run('refreshState()'), false);
    assert.equal(run('observedPlayback()'), null);
    assert.equal(run('$("#playPause").disabled'), true);
  }
});

test('external volume refresh updates focused slider and label; dragging alone protects input', async () => {
  const {run} = stateUI();
  run(`document.activeElement=$('#volume');
    api=async()=>({players:[{...state.players[0],volume_level:27}],queues:[]})`);
  await run('refreshState()');
  assert.equal(run('$("#volume").value'), 27);
  assert.equal(run('$("#volumeValue").textContent'), 27);
  run(`state.volumeDragging=true;state.players[0].volume_level=30;renderNow()`);
  assert.equal(run('$("#volume").value'), 27);
  run('state.volumeDragging=false;renderNow()');
  assert.equal(run('$("#volume").value'), 30);
});

test('local volume acceptance is not observation; bounded reads display eventual actual value', async () => {
  const {run} = stateUI();
  run(`let reads=0;let writes=0;
    api=async path=>path==='/api/volume'?(writes++,{}):
      (reads++,{players:[{...state.players[0],volume_level:reads>=3?25:10}],queues:[]});
    setVolume(25,true);`);
  // Drain the asynchronous writer and its finite reconciliation, without real clocks.
  for(let i=0;i<30;i++) await Promise.resolve();
  assert.equal(run('writes'), 1);
  assert.equal(run('state.pendingVolume'), null);
  assert.equal(run('$("#volume").value'), 25);
  assert.equal(run('reads'), 3);
});

test('transport rejection stays visible, releases the gate and never infers new playback', async () => {
  const {run} = stateUI();
  run(`api=async()=>{throw new Error('service failed')};renderNow()`);
  await run(`transport('play',$('#playPause'))`);
  assert.match(run('state.lastToast'), /service failed/);
  assert.equal(run('state.transportPending'), false);
  assert.equal(run('observedPlayback()'), 'paused');
});

test('selection change during reconciliation cannot confirm another destination', async () => {
  const {run} = stateUI();
  run(`api=async()=>{state.selectedPlayerId='RINCON_B';return {players:state.players,queues:[]}}`);
  await run(`reconcileState('transport','RINCON_A',()=>true)`);
  assert.equal(run(`state.actionTrace.some(e=>e.phase==='command_observed')`), false);
  assert.equal(run('state.reconciliations.size'), 0);
});

test('a hung state request is aborted, releases single-flight, and can recover', async () => {
  const {run, timers} = stateUI();
  run(`api=async(path,{signal})=>new Promise((resolve,reject)=>{
    signal.addEventListener('abort',()=>reject(new Error('read aborted')));
  });const hung=refreshState();`);
  assert.equal(timers.size, 1);
  for(const callback of timers.values()) callback();
  assert.equal(await run('hung'), false);
  assert.equal(run('state.refreshPromise'), null);
  assert.equal(run('state.stateAvailable'), false);
  run('api=async()=>({players:state.players,queues:[]})');
  assert.equal(await run('refreshState()'), true);
  assert.equal(run('observedPlayback()'), 'paused');
});

test('failed volume write clears intent without starting accepted-command reconciliation', async () => {
  const {run} = stateUI();
  run(`let reads=0;api=async path=>{
    if(path==='/api/volume') throw new Error('volume rejected');
    reads++;return {players:state.players,queues:[]};
  };setVolume(50,true);`);
  for(let i=0;i<20;i++) await Promise.resolve();
  assert.equal(run('reads'), 0);
  assert.equal(run('state.pendingVolume'), null);
  assert.equal(run('$("#volume").value'), 10);
  assert.match(run('state.lastToast'), /volume rejected/);
});

test('pointer release preserves final slider intent until change commits it', () => {
  const {run} = stateUI();
  run(`wire();renderNow();api=(path,options)=>{state.writtenVolume=JSON.parse(options.body).level;return new Promise(()=>{})};
    const slider=$('#volume');slider.listeners.pointerdown();slider.value=25;
    slider.listeners.input({target:slider});
    state.pendingVolume=null;renderNow(); // write has settled but observation is still 10
    slider.listeners.pointerup();
    slider.listeners.change({target:slider});`);
  assert.equal(run('$("#volume").value'), 25);
  assert.equal(run("state.writtenVolume"), 25);
});

test('favorites profile separates unique Spotify instances and retains shared items', () => {
  const {run} = standalone();
  run(`state.favoritesResults = {
    playlists: [
      {name:'David only',uri:'library://playlist/23',provider_instances:['spotify--Q5Eakxzw']},
      {name:'Kaelah only',uri:'library://playlist/81',provider_instances:['spotify--ooVGVwHn']},
      {name:'Shared',uri:'library://playlist/116',provider_instances:['spotify--Q5Eakxzw','spotify--ooVGVwHn']},
      {name:'Unknown',provider_instances:[]},
      {name:'Apple',provider_instances:['apple_music--x']}
    ], sonos_favorites:[{name:'Native Sonos'}]
  }`);
  assert.deepEqual(Array.from(run("filterFavoriteProfile(state.favoritesResults,'david').playlists"), x=>x.name),
    ['David only']);
  assert.deepEqual(Array.from(run("filterFavoriteProfile(state.favoritesResults,'kaelah').playlists"), x=>x.name),
    ['Kaelah only','Shared']);
  assert.equal(run("filterFavoriteProfile(state.favoritesResults,'david').sonos_favorites.length"), 0);
  assert.equal(run("filterFavoriteProfile(state.favoritesResults,'all').playlists.length"), 5);
  assert.equal(run("filterFavoriteProfile(state.favoritesResults,'all').sonos_favorites.length"), 1);
  assert.equal(run("filterFavoriteProfile(state.favoritesResults,'invalid').playlists.length"), 5);
});

test('David Spotify screenshot playlist IDs remain the only David playlists', () => {
  const {run} = standalone();
  run(`state.favoritesResults = {playlists: Array.from({length:120},(_,i)=>({name:String(i),uri:'library://playlist/'+i,provider_instances:['spotify--Q5Eakxzw','spotify--ooVGVwHn']}))}`);
  assert.deepEqual(Array.from(run("filterFavoriteProfile(state.favoritesResults,'david').playlists"), x=>x.uri),
    ['library://playlist/19','library://playlist/21','library://playlist/22','library://playlist/23','library://playlist/25','library://playlist/27']);
});

test('Apple Music favorites belong to David, not Kaelah, even with cross-provider mapping', () => {
  const {run} = standalone();
  run(`state.favoritesResults = {albums:[{name:'=',providers:['apple_music','spotify'],provider_instances:['apple_music--qpuX24cG','spotify--ooVGVwHn']}],tracks:[{name:'Hush Little Baby',providers:['apple_music'],provider_instances:['apple_music--qpuX24cG']}],playlists:[{name:'Apple playlist',providers:['apple_music'],provider_instances:['apple_music--qpuX24cG']}]}`);
  const apple = "filterFavoriteProvider(state.favoritesResults,'apple')";
  assert.equal(run(`filterFavoriteProfile(${apple},'all','apple').albums.length`), 1);
  assert.equal(run(`filterFavoriteProfile(${apple},'david','apple').albums.length`), 1);
  assert.equal(run(`filterFavoriteProfile(${apple},'david','apple').tracks.length`), 1);
  assert.equal(run(`filterFavoriteProfile(${apple},'kaelah','apple').albums.length`), 0);
  assert.equal(run(`filterFavoriteProfile(${apple},'kaelah','apple').playlists.length`), 0);
});
