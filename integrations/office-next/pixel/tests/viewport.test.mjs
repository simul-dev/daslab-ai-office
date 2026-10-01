import test from 'node:test';
import assert from 'node:assert/strict';
import {fitOfficeViewport} from '../src/viewport.mjs';

// Upstream's default editing grid leaves ten unused rows above the room.
const layout={cols:21,rows:22,tiles:Array.from({length:21*22},(_,index)=>{
  const row=Math.floor(index/21),col=index%21;
  return row>=10 && row<=20 && col<20?1:255;
})};
const furniture=[{x:16,y:144,sprite:Array.from({length:32},()=>Array(16).fill('#fff'))}];

function projectedBounds(viewport,size,dpr) {
  const offsetX=(size.width*dpr-layout.cols*16*viewport.zoom)/2+viewport.pan.x;
  const offsetY=(size.height*dpr-layout.rows*16*viewport.zoom)/2+viewport.pan.y;
  return {left:(offsetX+viewport.bounds.left*viewport.zoom)/dpr,right:(offsetX+viewport.bounds.right*viewport.zoom)/dpr,
    top:(offsetY+viewport.bounds.top*viewport.zoom)/dpr,bottom:(offsetY+viewport.bounds.bottom*viewport.zoom)/dpr};
}

test('sparse desktop office fits actual room at 2x and centers visible bounds',()=>{
  const size={width:790,height:456};
  const view=fitOfficeViewport(layout,furniture,size,1);
  assert.equal(view.zoom,2);
  const bounds=projectedBounds(view,size,1);
  assert.equal((bounds.left+bounds.right)/2,size.width/2);
  assert.equal((bounds.top+bounds.bottom)/2,size.height/2);
  assert.ok(bounds.top>=20 && bounds.bottom<=size.height-20);
  assert.ok(bounds.right-bounds.left>size.width*.8);
});

test('mobile device pixels produce the same centered office within CSS viewport',()=>{
  const size={width:360,height:270};
  for(const dpr of [1,2,3]) {
    const view=fitOfficeViewport(layout,furniture,size,dpr);
    const bounds=projectedBounds(view,size,dpr);
    assert.equal((bounds.left+bounds.right)/2,size.width/2);
    assert.equal((bounds.top+bounds.bottom)/2,size.height/2);
    assert.ok(bounds.left>=0 && bounds.right<=size.width);
    assert.ok(bounds.top>=0 && bounds.bottom<=size.height);
    assert.ok(bounds.right-bounds.left>size.width*.85);
  }
});

test('empty layout has finite safe fit',()=>{
  const view=fitOfficeViewport({...layout,tiles:layout.tiles.map(()=>255)},[],{width:640,height:480},1);
  assert.ok(Number.isFinite(view.zoom));assert.equal(view.pan.x,0);assert.equal(view.pan.y,0);
});
