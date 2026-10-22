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
    Date: DateMock, clock, crypto: {randomUUID}, console: {info(){}, warn(){}, error(){}},
    localStorage: {getItem(){return null}, setItem(){}}, sessionStorage: {setItem(){}},
    document: {querySelector(){return element}, querySelectorAll(){return []}, documentElement:{dataset:{}}},
    window: {setTimeout: timer, clearTimeout: id => timers.delete(id), customCards: []},
    setInterval(){}, clearInterval(){}, clearTimeout: id => timers.delete(id),
    HTMLElement: class {attachShadow(){this.shadowRoot = {getElementById(){return element}}}},
    customElements: {get(){}, define(){}}, navigator: {},
  });
  return {context, run: code => vm.runInContext(code, context)};
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
