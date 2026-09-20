#Interpret results from DEG analysis
#1. Make volcano plots
#2. Gene ontology enrichment

import pandas as pd
import numpy as np
from bioinfokit import visuz
import matplotlib.pyplot as plt

import requests
from requests.structures import CaseInsensitiveDict

#load data. Data should have columns for log2foldchange and adjusted P-value.
#For example the output of DESeq2
fc_data = pd.read_csv("log2fc.csv").dropna(subset = ['Gene name'])
plot_dir = "plots/"

genes = ['your', 'favorite', 'genes']

#Make volcano plots. Label genes if they're above thresholds in fold-change and significance
for gene in genes:
    fc_col = "log2FoldChange_" + gene
    pval_col = "padj_" + gene 


    fc_thresh = 1
    pval_thresh = 10**-17
    degs0 = fc_data[(fc_data[pval_col] < pval_thresh) & (np.abs(fc_data[fc_col]) > fc_thresh)]


    fc_thresh = 4
    pval_thresh = 10**-10
    degs1 = fc_data[(fc_data[pval_col] < pval_thresh) & (np.abs(fc_data[fc_col]) > fc_thresh)]


    fc_thresh = 6
    pval_thresh = 10**-5
    degs2 = fc_data[(fc_data[pval_col] < pval_thresh) & (np.abs(fc_data[fc_col]) > fc_thresh)]


    fc_thresh = 8
    pval_thresh = 0.05
    degs3 = fc_data[(fc_data[pval_col] < pval_thresh) & (np.abs(fc_data[fc_col]) > fc_thresh)]

    gene_list = tuple(degs0['Gene name']) + tuple(degs1['Gene name']) + tuple(degs2['Gene name']) + tuple(degs3['Gene name'])

    plot_subset = fc_data.dropna(subset = [fc_col, pval_col])

    visuz.GeneExpression.volcano(df=plot_subset, geneid = "Gene name", lfc=fc_col, pv=pval_col,
                                 dotsize = 3, valpha = 0.5,
                                 color = ("blue", "gray", "red"), xlm = (-6,16.5,1), ylm = (0,53,5), 
                                 sign_line = True, gfont = 5,
                                 genenames = gene_list, gstyle = 1, figname = 'volcano_'+gene)#, show = True)
    
#Do GO Enrichment Analysis using PANTHERDB (version 17)
#Note, the current version is 10.5281/zenodo.6399963

#Thresholds for enrichment
fc_thresh = 1
pval_thresh = 0.05
go_thresh = 0.05

url_base = "http://pantherdb.org/services/oai/pantherdb/enrich/overrep?geneInputList="
#9606 = human
#GO:0008150 = biological process
#alternatively, GO:0003674 = molecular function
url_tail = "&organism=9606&annotDataSet=GO%3A0008150&enrichmentTestType=FISHER&correction=FDR"
#url_tail = "&organism=9606&annotDataSet=GO%3A0003674&enrichmentTestType=FISHER&correction=FDR"

#Request headers
headers = CaseInsensitiveDict()
headers["connection"] = "keep-alive"
headers["keep-alive"] = "timeout=3600, max=100"

for gene in genes:
    fc_col = "log2FoldChange_" + gene
    pval_col = "padj_" + gene 
    
    degs_up = fc_data[(fc_data[pval_col] < pval_thresh) & (fc_data[fc_col] > fc_thresh)][['Gene name','Gene stable ID version',fc_col,pval_col]]
    
    #degs_up.to_csv(gene + '_up_3.csv')
    
    degs_down = fc_data[(fc_data[pval_col] < pval_thresh) & (fc_data[fc_col] < -fc_thresh)][['Gene name','Gene stable ID version',fc_col,pval_col]]
    
    #degs_down.to_csv(gene + '_down_3.csv')
    
    #Get enrichment data for upregulated genes
    gene_list = degs_up['Gene name'].tolist()
    
    if len(gene_list) > 1:
        full_url = url_base + ','.join(gene_list) + url_tail

        print("Requesting enrichment data for " + str(len(gene_list)) + " " + gene + " upregulated targets")
        #print(full_url)

        try:
            r = requests.post(full_url, headers = headers)
        except Exception as e:
            print("Failed: " + full_url)
            print(e)

        if r.status_code == 200:
            response_df = pd.DataFrame(r.json()['results']['result'])
            response_df[['id','label']] = pd.json_normalize(response_df['term'])
            response_df.drop(columns = 'term', inplace = True)
            signif = response_df[response_df['fdr'] < go_thresh]
            signif.to_csv(gene + '_' + str(fc_thresh) + "-fold_upregulated_GOs_biological_process.csv")
            #signif.to_csv(gene + '_' + str(fc_thresh) + "-fold_upregulated_GOs_molecular_function.csv")
        else: print("Failed: " + full_url)
        
    #Get enrichment data for downregulated genes
    gene_list = degs_down['Gene name'].tolist()
    full_url = url_base + ','.join(gene_list) + url_tail
    if len(gene_list) > 1:
        print("Requesting enrichment data for " + str(len(gene_list)) + " " + gene + " downregulated targets")

        try:
            r = requests.post(full_url)
        except:
            print("Failed: " + full_url)

        if r.status_code == 200:
            response_df = pd.DataFrame(r.json()['results']['result'])
            response_df[['id','label']] = pd.json_normalize(response_df['term'])
            response_df.drop(columns = 'term', inplace = True)
            signif = response_df[response_df['fdr'] < go_thresh]
            signif.to_csv(gene + '_' + str(fc_thresh) + "-fold_downregulated_GOs_biological_process.csv")
            #signif.to_csv(gene + '_' + str(fc_thresh) + "-fold_downregulated_GOs_molecular_function.csv")
        else: print("Request failed")
