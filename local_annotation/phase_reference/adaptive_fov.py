"""CPU optical field-of-view detection used only by opt-in phase inference.

Source frames are immutable. Missing evidence retains the complete frame.
No weights, patient information or service credentials are stored here.
"""
import math
import cv2
import numpy as np

VERSION='adaptive-fov-v1'
CFG={'sample_frames':32,'max_sample_frames':64,'detect_width':640,'minimum_score':.58,'bbox_margin_diameter':.02,'mask_margin_radius':.04}

def canonical_ellipse(value):
    (cx,cy),(w,h),angle=value
    a,b=w/2,h/2
    if a<b:a,b=b,a;angle+=90
    return np.array([cx,cy,a,b,angle%180],dtype=float)

def ellipse_points(e,angles,scale=1.):
    cx,cy,a,b,angle=e;theta=np.deg2rad(angle);u=a*scale*np.cos(angles);v=b*scale*np.sin(angles)
    return np.stack([cx+u*np.cos(theta)-v*np.sin(theta),cy+u*np.sin(theta)+v*np.cos(theta)],axis=-1)

def sample_gray(gray,points):
    return cv2.remap(gray,points[:,0].astype(np.float32).reshape(1,-1),points[:,1].astype(np.float32).reshape(1,-1),cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0).reshape(-1).astype(float)

def score_ellipse(gray,e,contour_error=.025,inner_scale=.94):
    H,W=gray.shape;cx,cy,a,b,angle=e
    if not (W*.12<cx<W*.88 and H*.08<cy<H*.92 and H*.16<b<H*.64 and H*.20<a<H*.72 and b/a>.64):return None
    angles=np.linspace(0,2*np.pi,180,endpoint=False)
    outer=ellipse_points(e,angles,1.065);inner=ellipse_points(e,angles,inner_scale)
    valid=(outer[:,0]>=0)&(outer[:,0]<W-1)&(outer[:,1]>=0)&(outer[:,1]<H-1)&(inner[:,0]>=0)&(inner[:,0]<W-1)&(inner[:,1]>=0)&(inner[:,1]<H-1)
    if valid.mean()<.62:return None
    contrast=sample_gray(gray,inner[valid])-sample_gray(gray,outer[valid]);support=float((contrast>4).mean())
    if support<.38:return None
    sharp=float(np.clip(np.median(np.maximum(0,contrast))/35,0,1));shape=float(np.clip(1-contour_error/.13,0,1))
    size=float(np.clip(math.sqrt(math.pi*a*b/(W*H))/.75,0,1))
    score=.46*support+.20*sharp+.17*shape+.17*size
    return {'ellipse':e.tolist(),'score':float(score),'edge_support':support,'contrast':float(np.median(contrast)),'method':'contour'}

def detect(frame):
    oh,ow=frame.shape[:2];scale=min(1.,CFG['detect_width']/ow);im=cv2.resize(frame,(round(ow*scale),round(oh*scale)),interpolation=cv2.INTER_AREA)
    gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY);blur=cv2.GaussianBlur(gray,(5,5),0)
    H,W=gray.shape;found=[]
    for level in [4,8,12,18,28,45,65,90,120,160,210]:
        mask=(blur>level).astype(np.uint8)*255
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)))
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3)))
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
        for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:4]:
            area=cv2.contourArea(contour)
            if len(contour)<40 or area<H*H*.06:continue
            points=contour[:,0,:].astype(np.float32)
            try:e=canonical_ellipse(cv2.fitEllipse(points))
            except cv2.error:continue
            for _ in range(2):
                q=points-e[:2];theta=np.deg2rad(e[4]);u=q[:,0]*np.cos(theta)+q[:,1]*np.sin(theta);v=-q[:,0]*np.sin(theta)+q[:,1]*np.cos(theta)
                radial=np.sqrt((u/max(1,e[2]))**2+(v/max(1,e[3]))**2);residual=np.abs(radial-1)
                keep=residual<max(.035,float(np.quantile(residual,.78)))
                if keep.sum()<35:break
                points=points[keep]
                e=canonical_ellipse(cv2.fitEllipse(points))
            if not np.all(np.isfinite(e)) or min(e[2:4])<1:continue
            q=contour[:,0,:]-e[:2];theta=np.deg2rad(e[4]);u=q[:,0]*np.cos(theta)+q[:,1]*np.sin(theta);v=-q[:,0]*np.sin(theta)+q[:,1]*np.cos(theta)
            error=float(np.quantile(np.abs(np.sqrt((u/e[2])**2+(v/e[3])**2)-1),.8))
            if error>.15 or not .62<area/(math.pi*e[2]*e[3])<1.2:continue
            value=score_ellipse(blur,e,error)
            if value is None or value['score']<CFG['minimum_score']:continue
            value['threshold']=level;value['contour_error']=error
            found.append(value)
    # A gradient-circle candidate helps when low-threshold glare joins the background.
    circles=cv2.HoughCircles(blur,cv2.HOUGH_GRADIENT,dp=1.2,minDist=H*.18,param1=85,param2=32,minRadius=round(H*.18),maxRadius=round(H*.57))
    if circles is not None:
        for cx,cy,radius in circles[0][:8]:
            e=np.array([cx,cy,radius,radius,0.]);value=score_ellipse(blur,e,.025)
            if value and value['score']>=CFG['minimum_score']:
                value['method']='gradient_circle';found.append(value)
    found.sort(key=lambda v:v['score'],reverse=True)
    selected=[]
    for value in found:
        e=np.array(value['ellipse'])
        if any(np.linalg.norm(e[:2]-np.array(s['ellipse'])[:2])<H*.015 and np.max(np.abs(e[2:4]-np.array(s['ellipse'])[2:4]))<H*.015 for s in selected):continue
        selected.append(value)
        if len(selected)>=5:break
    for value in selected:
        e=value['ellipse'];value['ellipse']=[e[0]/scale,e[1]/scale,e[2]/scale,e[3]/scale,e[4]]
    return selected

def distance(a,b,width,height):
    a=np.asarray(a);b=np.asarray(b)
    return max(abs(a[0]-b[0])/(width*.035),abs(a[1]-b[1])/(height*.045),abs(a[2]-b[2])/(max(a[2],b[2])*.12),abs(a[3]-b[3])/(max(a[3],b[3])*.12))

def cluster_candidates(samples,width,height):
    candidates=[(index,value)for index,row in enumerate(samples)for value in row['candidates']]
    clusters=[]
    for index,value in sorted(candidates,key=lambda v:v[1]['score'],reverse=True):
        matches=[(distance(value['ellipse'],group['center'],width,height),group)for group in clusters]
        best=min(matches,key=lambda v:v[0])if matches else None
        if best and best[0]<1:
            group=best[1]
            previous=group['members'].get(index)
            if previous is None or value['score']>previous['score']:group['members'][index]=value
            group['center']=np.median([v['ellipse']for v in group['members'].values()],axis=0).tolist()
        else:clusters.append({'center':value['ellipse'],'members':{index:value}})
    groups=[]
    for group in clusters:
        members=group['members'];count=len(members)
        if count<3:continue
        e=np.array([v['ellipse']for v in members.values()]);center=np.median(e[:,:2],axis=0);axes=np.quantile(e[:,2:4],.95,axis=0)
        if axes[1]/axes[0]>.92:axes[:]=max(axes);angle=0.
        else:angle=float(np.rad2deg(np.angle(np.mean(np.exp(2j*np.deg2rad(e[:,4])))))/2%180)
        fitted=[*center,*axes,angle];score=float(np.mean([v['score']for v in members.values()]))
        groups.append({'ellipse':[float(v)for v in fitted],'support':count,'mean_score':score,'sample_indices':sorted(members),'rank':count*score})
    groups.sort(key=lambda v:v['rank'],reverse=True)
    return groups

def ellipse_mask(shape,ellipse,expand=0.):
    h,w=shape[:2];mask=np.zeros((h,w),np.uint8);cx,cy,a,b,angle=ellipse
    cv2.ellipse(mask,(round(cx),round(cy)),(max(1,round(a*(1+expand))),max(1,round(b*(1+expand)))),angle,0,360,255,-1)
    return mask

def crop_box(ellipse,width,height):
    cx,cy,a,b,angle=ellipse;theta=math.radians(angle);a*=1+CFG['mask_margin_radius'];b*=1+CFG['mask_margin_radius'];pad=2*max(a,b)*CFG['bbox_margin_diameter']
    xradius=math.sqrt((a*math.cos(theta))**2+(b*math.sin(theta))**2);yradius=math.sqrt((a*math.sin(theta))**2+(b*math.cos(theta))**2)
    return [max(0,math.floor(cx-xradius-pad)),max(0,math.floor(cy-yradius-pad)),min(width,math.ceil(cx+xradius+pad)),min(height,math.ceil(cy+yradius+pad))]

def find_text(frame):
    h,w=frame.shape[:2];scale=min(1.,640/w);im=cv2.resize(frame,(round(w*scale),round(h*scale)));b,g,r=cv2.split(im)
    # Restrict the overlay detector to outer top/bottom bands and aligned glyph rows.
    colored=((r>130)&(g>120)&(b<120)&(np.abs(r.astype(float)-g.astype(float))<105)).astype(np.uint8)*255
    region=np.zeros_like(colored);region[:round(colored.shape[0]*.23)]=255;region[round(colored.shape[0]*.86):]=255;colored&=region
    # Group original glyph components by baseline before joining date/time runs.
    # A wide space between the date and clock must not leave the date unmasked.
    n,_,stats,_=cv2.connectedComponentsWithStats(colored,8)
    glyphs=[v for v in stats[1:] if v[4]>=2 and 3<=v[3]<=im.shape[0]*.09 and v[2]<=v[3]*2]
    rows=[]
    for x,y,bw,bh,area in sorted(glyphs,key=lambda v:(v[1]+v[3]/2,v[0])):
        cy=y+bh/2
        row=next((v for v in rows if abs(v['cy']-cy)<=max(bh,v['height'])*.5),None)
        if row is None:
            row={'cy':cy,'height':bh,'glyphs':[]};rows.append(row)
        row['glyphs'].append((int(x),int(y),int(bw),int(bh)))
    boxes=[]
    for row in rows:
        runs=[]
        for glyph in sorted(row['glyphs']):
            x,y,bw,bh=glyph
            if not runs or x-max(v[0]+v[2] for v in runs[-1])>max(im.shape[1]*.06,row['height']*4):runs.append([])
            runs[-1].append(glyph)
        for run in runs:
            x=min(v[0] for v in run);y=min(v[1] for v in run)
            x1=max(v[0]+v[2] for v in run);y1=max(v[1]+v[3] for v in run)
            if len(run)>=8 and x1-x>=im.shape[1]*.12 and 4<(x1-x)/max(1,y1-y)<40:
                boxes.append([max(0,math.floor(x/scale)-2),max(0,math.floor(y/scale)-2),min(w,math.ceil(x1/scale)+2),min(h,math.ceil(y1/scale)+2)])
    return boxes

def stabilize_text(text_samples,width,height):
    groups=[]
    for row in text_samples:
        for box in row:
            match=next((g for g in groups if abs(g['center'][0]-box[0])<width*.03 and abs(g['center'][1]-box[1])<height*.035),None)
            if match is None:groups.append({'center':box,'boxes':[box]})
            else:match['boxes'].append(box);match['center']=np.median(match['boxes'],axis=0).tolist()
    result=[]
    for group in groups:
        if len(group['boxes'])<max(3,len(text_samples)*.35):continue
        b=np.array(group['boxes']);result.append([max(0,int(np.min(b[:,0]))-2),max(0,int(np.min(b[:,1]))-2),min(width,int(np.max(b[:,2]))+2),min(height,int(np.max(b[:,3]))+2)])
    return result

def apply(frame,ellipse,text_boxes):
    h,w=frame.shape[:2]
    mask=np.ones((h,w),np.uint8)*255 if ellipse is None else ellipse_mask(frame.shape,ellipse,CFG['mask_margin_radius'])
    b,g,r=cv2.split(frame)
    glyph=((r>130)&(g>120)&(b<130)&(np.abs(r.astype(float)-g.astype(float))<110)).astype(np.uint8)*255
    allowed=np.zeros_like(mask)
    for x0,y0,x1,y1 in text_boxes:allowed[y0:y1,x0:x1]=255
    glyph&=allowed;glyph=cv2.dilate(glyph,np.ones((3,3),np.uint8));mask[glyph>0]=0
    processed=cv2.bitwise_and(frame,frame,mask=mask);box=crop_box(ellipse,w,h)if ellipse is not None else[0,0,w,h];x0,y0,x1,y1=box
    return processed[y0:y1,x0:x1],box,mask

def input_square(frame,size=224):
    h,w=frame.shape[:2];side=max(h,w);square=np.zeros((side,side,3),np.uint8);y=(side-h)//2;x=(side-w)//2;square[y:y+h,x:x+w]=frame
    return cv2.resize(square,(size,size),interpolation=cv2.INTER_AREA)


class AdaptiveFOV:
    """Bounded initialization, then one lightweight validation per video second.

    The sequential feature decoder calls observe(), including frames that are
    not CNN samples. Thus a stride/profile change cannot skip the 1 Hz check.
    Geometry changes apply from the observed frame, without extrapolating a
    later circle into an earlier unlocated segment.
    """
    def __init__(self, path, meta, image_size=224, check=lambda: None):
        check()
        # OpenCV otherwise starts 96 workers on this GPU host for each 640 px
        # fit. A single CPU worker is faster here and bounds transient load.
        cv2.setNumThreads(1)
        self.width, self.height = meta['width'], meta['height']
        self.duration, self.image_size = meta['duration_sec'], image_size
        self.check = check
        self.groups, self.text_boxes, self.intervals = [], [], []
        self.current, self.signature, self.last_second = None, None, -1
        self.pending_ellipse, self.pending_count = None, 0
        self.pending_second = -2
        self.stats = {'initialization_frames': 0, 'light_checks': 0, 'refits': 0}
        self._mask, self._box = None, [0, 0, self.width, self.height]
        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                raise ValueError('Cannot open video for FOV initialization')
            samples, texts = [], []
            # At most 32 small candidate records; never retain the video/frames.
            count = min(32, max(8, math.ceil(self.duration / 3)))
            for t in np.linspace(0, max(0, self.duration - 1/meta['fps']), count):
                check()
                cap.set(cv2.CAP_PROP_POS_MSEC, float(t)*1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                samples.append({'candidates': detect(frame)})
                texts.append(find_text(frame))
            self.stats['initialization_frames'] = len(samples)
            if not samples:
                raise ValueError('No decodable initialization frames')
            groups = cluster_candidates(samples, self.width, self.height)
            self.groups = [groups[0]['ellipse']] if groups else []
            for group in groups[1:]:
                if (group['support'] >= max(4,len(samples)*.18) and group['mean_score'] > .72
                        and all(distance(group['ellipse'],known,self.width,self.height)>1.5 for known in self.groups)):
                    self.groups.append(group['ellipse'])
                if len(self.groups)>=4:break
            self.text_boxes = stabilize_text(texts, self.width, self.height)
        finally:
            cap.release()

    @staticmethod
    def _evidence(gray, ellipse, scale):
        if ellipse is None:
            return False
        small = np.asarray(ellipse, dtype=float).copy()
        small[:4] *= scale
        score = score_ellipse(gray, small, inner_scale=.90)
        return bool(score and score['score'] >= .58 and score['edge_support'] >= .52)

    def observe(self, frame, time_sec):
        second = int(time_sec + 1e-8)
        if second == self.last_second:
            return
        self.check()
        self.last_second = second
        self.stats['light_checks'] += 1
        scale = min(1., CFG['detect_width']/self.width)
        small = cv2.resize(frame, (round(self.width*scale), round(self.height*scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5,5), 0)
        signature = cv2.resize(gray, (32,24)).astype(np.float32)
        ellipse = self.current
        if not self._evidence(gray, ellipse, scale):
            ellipse = next((e for e in self.groups if self._evidence(gray, e, scale)), None)
            changed_scene = self.signature is None or float(np.mean(np.abs(signature-self.signature))) > 5
            # An invalid former circle always triggers a full fit. In fallback,
            # unchanged full-frame/black imagery does not cause repeated fits.
            if ellipse is None and (self.current is not None or changed_scene or self.pending_ellipse is not None):
                self.stats['refits'] += 1
                values = detect(frame)
                if values:
                    candidate = values[0]['ellipse']
                    known = next((e for e in self.groups if distance(e, candidate, self.width, self.height)<1), None)
                    if known is not None:
                        ellipse = known
                        self.pending_ellipse, self.pending_count = None, 0
                    elif values[0]['score'] >= CFG['minimum_score']:
                        consistent = (self.pending_second == second-1 and self.pending_ellipse is not None
                                      and distance(self.pending_ellipse,candidate,self.width,self.height)<.5)
                        self.pending_count = self.pending_count+1 if consistent else 1
                        self.pending_ellipse = candidate
                        self.pending_second = second
                        # A single bright anatomical feature is not sufficient
                        # evidence for a previously unseen smaller optical FOV.
                        if self.pending_count >= 2:
                            ellipse = candidate
                    else:
                        self.pending_ellipse, self.pending_count = None, 0
                else:
                    self.pending_ellipse, self.pending_count = None, 0
            self.signature = signature
        if self.intervals and ellipse is not None and self.current is not None and distance(ellipse, self.current, self.width, self.height)<.25:
            ellipse = self.current
        changed = not self.intervals or (ellipse is None) != (self.current is None) or (ellipse is not None and ellipse != self.current)
        if changed:
            if len(self.intervals) >= 99:
                # Keep the metadata/storage contract bounded and be conservative
                # for highly edited videos instead of inventing a stable circle.
                ellipse = None
            if self.intervals and ellipse == self.current:
                return
            if self.intervals:
                self.intervals[-1]['end_sec'] = min(time_sec, self.duration)
            self.current = ellipse
            self._box = crop_box(ellipse, self.width, self.height) if ellipse is not None else [0,0,self.width,self.height]
            self._mask = ellipse_mask(frame.shape, ellipse, CFG['mask_margin_radius']) if ellipse is not None else None
            self.intervals.append({'start_sec': min(time_sec,self.duration), 'end_sec': self.duration,
                                   'ellipse': ellipse, 'box': self._box})

    def image(self, frame):
        x0,y0,x1,y1 = self._box
        cropped = frame[y0:y1,x0:x1].copy()
        if self._mask is not None:
            cropped = cv2.bitwise_and(cropped, cropped, mask=self._mask[y0:y1,x0:x1])
        # Only confirmed overlay rows are inspected; preserve similarly colored
        # tissue elsewhere. Mask glyph pixels, not the whole bounding rectangle.
        for tx0,ty0,tx1,ty1 in self.text_boxes:
            a,b,c,d = max(tx0,x0),max(ty0,y0),min(tx1,x1),min(ty1,y1)
            if a>=c or b>=d:
                continue
            roi = cropped[b-y0:d-y0,a-x0:c-x0]
            blue,green,red = cv2.split(roi)
            glyph = ((red>130)&(green>120)&(blue<130)&(np.abs(red.astype(float)-green.astype(float))<110)).astype(np.uint8)
            glyph = cv2.dilate(glyph,np.ones((3,3),np.uint8))
            roi[glyph>0] = 0
        return cv2.cvtColor(input_square(cropped,self.image_size),cv2.COLOR_BGR2RGB)

    def record(self):
        # crop_box is a compatibility footprint for older gateways; intervals
        # retain the actual per-time geometry, including full-frame fallback.
        boxes = [v['box'] for v in self.intervals]
        box = [min(v[0] for v in boxes),min(v[1] for v in boxes),max(v[2] for v in boxes),max(v[3] for v in boxes)]
        return {'mode':'center_norm', 'image_size':self.image_size,
                'source_type':VERSION, 'strategy':VERSION, 'crop_box':box,
                'intervals':self.intervals, 'text_boxes':self.text_boxes,
                'fallback':'full_frame_letterbox', 'check_interval_sec':1,
                'statistics':self.stats}

