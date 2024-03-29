from matplotlib.image import imread
import scipy.io
import dgl
import math
import torch
import numpy as np
from model import *
import argparse
from sklearn import metrics
import time

from fairness import Fairness

parser = argparse.ArgumentParser(description='for Pokec Dataset')

parser.add_argument('--n_epoch', type=int, default=50)
parser.add_argument('--batch_size', type=int, default=512)
parser.add_argument('--seed', type=int, default=7)
parser.add_argument('--n_hid',   type=int, default=32)
parser.add_argument('--n_inp',   type=int, default=200)
parser.add_argument('--clip',    type=int, default=1.0)
parser.add_argument('--max_lr',  type=float, default=1e-2)
parser.add_argument('--label',  type=str, default='gender')
parser.add_argument('--gpu',  type=int, default=0, choices=[0,1,2,3,4,5,6,7])
parser.add_argument('--graph',  type=str, default='G_ori')
parser.add_argument('--model',  type=str, default='RHGN', choices=['RHGN','RGCN'])
parser.add_argument('--data_dir',  type=str, default='../data/sample')
parser.add_argument('--patience', type=int, default=10)
parser.add_argument('--sens_attr', type=str, default='gender')
parser.add_argument('--log_tags', type=str, default='')

args = parser.parse_args()
'''Fixed random seeds'''
np.random.seed(args.seed)
torch.manual_seed(args.seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)


def get_n_params(model):
    pp=0
    for p in list(model.parameters()):
        nn=1
        for s in list(p.size()):
            nn = nn*s
        pp += nn
    return pp


def Batch_train(model):
    tic = time.perf_counter() # start counting time

    best_val_acc = 0
    best_test_acc = 0
    train_step = 0
    Minloss_val = 10000.0
    for epoch in np.arange(args.n_epoch) + 1:
        model.train()
        '''---------------------------train------------------------'''
        total_loss = 0
        total_acc = 0
        count = 0
        for input_nodes, output_nodes, blocks in train_dataloader:
            Batch_logits,Batch_labels = model(input_nodes,output_nodes,blocks, out_key='user1',label_key=args.label, is_train=True)

            # The loss is computed only for labeled nodes.
            loss = F.cross_entropy(Batch_logits, Batch_labels)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
            optimizer.step()
            train_step += 1
            scheduler.step(train_step)

            acc = torch.sum(Batch_logits.argmax(1) == Batch_labels).item()
            total_loss += loss.item() * len(output_nodes['user'].cpu())
            total_acc += acc
            count += len(output_nodes['user'].cpu())

        train_loss, train_acc = total_loss / count, total_acc / count

        if epoch % 1 == 0:
            model.eval()
            '''-------------------------val-----------------------'''
            with torch.no_grad():
                total_loss = 0
                total_acc = 0
                count = 0
                preds=[]
                labels=[]
                for input_nodes, output_nodes, blocks in val_dataloader:
                    Batch_logits,Batch_labels = model(input_nodes, output_nodes,blocks, out_key='user1',label_key=args.label, is_train=False)
                    loss = F.cross_entropy(Batch_logits, Batch_labels)
                    acc   = torch.sum(Batch_logits.argmax(1)==Batch_labels).item()
                    preds.extend(Batch_logits.argmax(1).tolist())
                    labels.extend(Batch_labels.tolist())
                    total_loss += loss.item() * len(output_nodes['user'].cpu())
                    total_acc +=acc
                    count += len(output_nodes['user'].cpu())

                val_f1 = metrics.f1_score(preds, labels, average='macro')
                val_loss,val_acc   = total_loss / count, total_acc / count
                '''------------------------test----------------------'''
                total_loss = 0
                total_acc = 0
                count = 0
                preds = []
                labels = []

                for input_nodes, output_nodes, blocks in test_dataloader:
                    Batch_logits, Batch_labels = model(input_nodes, output_nodes, blocks, out_key='user1', label_key=args.label, is_train=False)
                    loss = F.cross_entropy(Batch_logits, Batch_labels)
                    acc   = torch.sum(Batch_logits.argmax(1)==Batch_labels).item()
                    preds.extend(Batch_logits.argmax(1).tolist())
                    labels.extend(Batch_labels.tolist())
                    total_loss += loss.item() * len(output_nodes['user'].cpu())
                    total_acc +=acc
                    count += len(output_nodes['user'].cpu())
                   

                test_f1 = metrics.f1_score(preds,labels, average='macro')
                test_loss,test_acc   = total_loss / count, total_acc / count
                if  val_acc   > best_val_acc:
                    Minloss_val = val_loss
                    best_val_acc = val_acc
                    best_test_acc = test_acc
                print('Epoch: %d LR: %.5f Loss %.4f, val loss %.4f, Val Acc %.4f (Best %.4f), Test Acc %.4f (Best %.4f)' % (
                    epoch,
                    optimizer.param_groups[0]['lr'],
                    train_loss,
                    val_loss,
                    val_acc,
                    best_val_acc,
                    test_acc,
                    best_test_acc,
                ))
                print('\t\tval_f1 %.4f test_f1 \033[1;33m %.4f \033[0m' % (val_f1, test_f1))
            torch.cuda.empty_cache()

    # Classification reports
    confusion_matrix = metrics.confusion_matrix(labels, preds)
    print(confusion_matrix)
    # fpr, tpr, _ = metrics.roc_curve(labels, preds)
    # auc = metrics.auc(fpr, tpr)
    # print("AUC:", auc)
    classification_report = metrics.classification_report(labels, preds, digits=4)
    print(classification_report)

    toc = time.perf_counter() # stop counting time
    elapsed_time = (toc-tic)/60
    print("\nElapsed time: {:.4f} minutes".format(elapsed_time))

    return labels, preds


######################################################################
device = torch.device("cuda:{}".format(args.gpu))

'''Loading charts and labels'''
G=torch.load('{}/{}.pkl'.format(args.data_dir,args.graph))
print(G)
labels=G.nodes['user1'].data[args.label]
print(labels.max().item()+1)

# generate train/val/test split
pid = np.arange(len(labels))
shuffle = np.random.permutation(pid)
train_idx = torch.tensor(shuffle[0:int(len(labels)*0.75)]).long()
val_idx = torch.tensor(shuffle[int(len(labels)*0.75):int(len(labels)*0.875)]).long()
test_idx = torch.tensor(shuffle[int(len(labels)*0.875):]).long()

print("train_idx:", train_idx.shape)
print("val_idx:", val_idx.shape)
print("test_idx:", test_idx.shape, type(test_idx), test_idx)

node_dict = {}
edge_dict = {}
for ntype in G.ntypes:
    node_dict[ntype] = len(node_dict)
for etype in G.etypes:
    edge_dict[etype] = len(edge_dict)
    G.edges[etype].data['id'] = torch.ones(G.number_of_edges(etype), dtype=torch.long) * edge_dict[etype]

# Initialize input feature
# import fasttext
# model = fasttext.load_model('../jd_data/fasttext/fastText/cc.zh.200.bin')
# sentence_dic=torch.load('../jd_data/sentence_dic.pkl')
# sentence_vec = [model.get_sentence_vector(sentence_dic[k]) for k, v in enumerate(G.nodes('item').tolist())]
# for ntype in G.ntypes:
#     if ntype=='item':
#         emb=nn.Parameter(torch.Tensor(sentence_vec), requires_grad = False)
#     else:
#         emb = nn.Parameter(torch.Tensor(G.number_of_nodes(ntype), 200), requires_grad = False)
#         nn.init.xavier_uniform_(emb)
#     G.nodes[ntype].data['inp'] = emb

for ntype in G.ntypes:
    emb = nn.Parameter(torch.Tensor(G.number_of_nodes(ntype), args.n_inp), requires_grad = False)
    nn.init.xavier_uniform_(emb)
    G.nodes[ntype].data['inp'] = emb


G = G.to(device)
train_idx_item=torch.tensor(shuffle[0:int(G.number_of_nodes('user2') * 0.75)]).long()
val_idx_item = torch.tensor(shuffle[int(G.number_of_nodes('user2')*0.75):int(G.number_of_nodes('user2')*0.875)]).long()
test_idx_item = torch.tensor(shuffle[int(G.number_of_nodes('user2')*0.875):]).long()
'''Sampling'''
sampler = dgl.dataloading.MultiLayerFullNeighborSampler(2)

train_dataloader = dgl.dataloading.DataLoader(
    G, {'user':train_idx.to(device)}, sampler,
    batch_size=args.batch_size,
    shuffle=False,
    drop_last=False,
    device=device)

val_dataloader = dgl.dataloading.DataLoader(
    G, {'user':val_idx.to(device)}, sampler,
    batch_size=args.batch_size,
    shuffle=False,
    drop_last=False,
    device=device)

test_dataloader = dgl.dataloading.DataLoader(
    G, {'user':test_idx.to(device)}, sampler,
    batch_size=args.batch_size,
    shuffle=False,
    drop_last=False,
    device=device)


if args.model=='RHGN':
    compl_feature = torch.load('{}/compl_feature.npy'.format(args.data_dir))
    lang_feature = torch.load('{}/lang_feature.npy'.format(args.data_dir))
    hobbies_feature = torch.load('{}/hobbies_feature.npy'.format(args.data_dir))

    model = pokec_RHGN(G,
                node_dict, edge_dict,
                n_inp=args.n_inp,
                n_hid=args.n_hid,
                n_out=labels.max().item()+1,
                n_layers=2,
                n_heads=4,
                compl_feature=compl_feature,
                lang_feature=lang_feature,
                hobbies_feature=hobbies_feature,
            
                use_norm = True).to(device)
    optimizer = torch.optim.AdamW(model.parameters())

    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, epochs=args.n_epoch,
                                                    steps_per_epoch=int(train_idx.shape[0]/args.batch_size)+1,max_lr = args.max_lr)
    print('Training RHGN with #param: %d' % (get_n_params(model)))
    targets, predictions = Batch_train(model)

    # Compute fairness
    fair_obj = Fairness(G, test_idx, targets, predictions, args.sens_attr)
    fair_obj.statistical_parity()
    fair_obj.equal_opportunity()
    fair_obj.overall_accuracy_equality()
    fair_obj.treatment_equality()